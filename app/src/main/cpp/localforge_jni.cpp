// LocalForge native bridge: loads GGUF models with llama.cpp and streams tokens to Kotlin.
#include <jni.h>
#include <string>
#include <vector>
#include <atomic>
#include <cstring>
#include <algorithm>
#include "llama.h"
#include "ggml-backend.h"

#ifdef __ANDROID__
#include <android/log.h>
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, "LocalForge", __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "LocalForge", __VA_ARGS__)
#else
#include <cstdio>
#define LOGI(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } while (0)
#define LOGE(...) LOGI(__VA_ARGS__)
#endif

namespace {

struct Engine {
    llama_model * model = nullptr;
    llama_context * ctx = nullptr;
    const llama_vocab * vocab = nullptr;
    std::atomic<bool> stop{false};
    std::vector<llama_token> cached; // tokens currently in the KV cache (reused across turns)
};

std::string jstr(JNIEnv * env, jstring s) {
    if (!s) return {};
    const char * c = env->GetStringUTFChars(s, nullptr);
    std::string out(c ? c : "");
    if (c) env->ReleaseStringUTFChars(s, c);
    return out;
}

// Text from Kotlin arrives as real UTF-8 bytes. GetStringUTFChars gives "modified UTF-8", where an emoji
// becomes two 3-byte halves the tokenizer can't read (and a half emoji cut off by a page snippet breaks it).
std::string jbytes(JNIEnv * env, jbyteArray a) {
    if (!a) return {};
    jsize n = env->GetArrayLength(a);
    std::string out((size_t) n, '\0');
    if (n > 0) env->GetByteArrayRegion(a, 0, n, (jbyte *) out.data());
    return out;
}

// Java's NewStringUTF uses "modified UTF-8", so we hand raw bytes to Kotlin instead.
jbyteArray toBytes(JNIEnv * env, const std::string & s) {
    jbyteArray arr = env->NewByteArray((jsize) s.size());
    if (arr && !s.empty()) env->SetByteArrayRegion(arr, 0, (jsize) s.size(), (const jbyte *) s.data());
    return arr;
}

// Number of bytes at the end of `s` that form an incomplete UTF-8 sequence.
size_t incompleteUtf8Tail(const std::string & s) {
    size_t n = s.size();
    for (size_t i = 1; i <= 4 && i <= n; ++i) {
        unsigned char c = (unsigned char) s[n - i];
        if ((c & 0xC0) == 0x80) continue;          // continuation byte
        size_t need = 1;
        if ((c & 0xE0) == 0xC0) need = 2;
        else if ((c & 0xF0) == 0xE0) need = 3;
        else if ((c & 0xF8) == 0xF0) need = 4;
        return need > i ? i : 0;
    }
    return 0;
}

std::string tokenToPiece(const llama_vocab * vocab, llama_token t) {
    char buf[256];
    int n = llama_token_to_piece(vocab, t, buf, sizeof(buf), 0, false);
    if (n < 0) {
        std::string big((size_t) -n, '\0');
        n = llama_token_to_piece(vocab, t, big.data(), (int32_t) big.size(), 0, false);
        return n > 0 ? big.substr(0, (size_t) n) : std::string();
    }
    return std::string(buf, (size_t) n);
}

} // namespace

extern "C" {

// Loads the fastest CPU backend variant for this chip from the app's native lib folder.
// Returns the number of compute devices found (0 means nothing usable loaded).
JNIEXPORT jint JNICALL
Java_com_localforge_app_engine_LlamaEngine_nativeInit(JNIEnv * env, jclass, jstring jLibDir) {
    std::string dir = jstr(env, jLibDir);
    if (!dir.empty()) ggml_backend_load_all_from_path(dir.c_str());
    llama_backend_init();
    size_t n = ggml_backend_dev_count();
    for (size_t i = 0; i < n; ++i) {
        ggml_backend_dev_t d = ggml_backend_dev_get(i);
        LOGI("backend device %zu: %s (%s)", i, ggml_backend_dev_name(d), ggml_backend_dev_description(d));
    }
    return (jint) n;
}

JNIEXPORT jlong JNICALL
Java_com_localforge_app_engine_LlamaEngine_nativeLoad(JNIEnv * env, jclass, jstring jpath, jint nCtx, jint nThreads) {
    std::string path = jstr(env, jpath);
    llama_model_params mp = llama_model_default_params();
    mp.n_gpu_layers = 0;
    llama_model * model = llama_model_load_from_file(path.c_str(), mp);
    if (!model) {
        LOGE("failed to load model %s", path.c_str());
        return 0;
    }
    llama_context_params cp = llama_context_default_params();
    cp.n_ctx = (uint32_t) (nCtx > 0 ? nCtx : 4096);
    cp.n_batch = 512;
    cp.n_ubatch = 512;
    cp.n_threads = nThreads > 0 ? nThreads : 4;
    cp.n_threads_batch = cp.n_threads;
    llama_context * ctx = llama_init_from_model(model, cp);
    if (!ctx) {
        LOGE("failed to create context");
        llama_model_free(model);
        return 0;
    }
    auto * e = new Engine();
    e->model = model;
    e->ctx = ctx;
    e->vocab = llama_model_get_vocab(model);
    LOGI("model loaded: %s", path.c_str());
    return (jlong) e;
}

JNIEXPORT void JNICALL
Java_com_localforge_app_engine_LlamaEngine_nativeFree(JNIEnv *, jclass, jlong handle) {
    auto * e = (Engine *) handle;
    if (!e) return;
    if (e->ctx) llama_free(e->ctx);
    if (e->model) llama_model_free(e->model);
    delete e;
}

JNIEXPORT void JNICALL
Java_com_localforge_app_engine_LlamaEngine_nativeStop(JNIEnv *, jclass, jlong handle) {
    auto * e = (Engine *) handle;
    if (e) e->stop = true;
}

// Number of tokens `text` takes (used to fit the conversation into the context window).
JNIEXPORT jint JNICALL
Java_com_localforge_app_engine_LlamaEngine_nativeCountTokens(JNIEnv * env, jclass, jlong handle, jbyteArray jtext) {
    auto * e = (Engine *) handle;
    if (!e) return -1;
    std::string text = jbytes(env, jtext);
    if (text.empty()) return 0;
    int n = llama_tokenize(e->vocab, text.c_str(), (int32_t) text.size(), nullptr, 0, false, true);
    return (jint) (n < 0 ? -n : n);
}

// The context size the model was actually loaded with.
JNIEXPORT jint JNICALL
Java_com_localforge_app_engine_LlamaEngine_nativeCtxSize(JNIEnv *, jclass, jlong handle) {
    auto * e = (Engine *) handle;
    return e ? (jint) llama_n_ctx(e->ctx) : 0;
}

// Formats a conversation with the model's own chat template (falls back to ChatML).
JNIEXPORT jbyteArray JNICALL
Java_com_localforge_app_engine_LlamaEngine_nativeFormatChat(JNIEnv * env, jclass, jlong handle,
                                                            jobjectArray roles, jobjectArray contents) {
    auto * e = (Engine *) handle;
    if (!e) return nullptr;
    jsize n = env->GetArrayLength(roles);
    std::vector<std::string> r(n), c(n);
    std::vector<llama_chat_message> msgs(n);
    size_t total = 0;
    for (jsize i = 0; i < n; ++i) {
        r[i] = jstr(env, (jstring) env->GetObjectArrayElement(roles, i));
        c[i] = jbytes(env, (jbyteArray) env->GetObjectArrayElement(contents, i));
        total += r[i].size() + c[i].size();
    }
    for (jsize i = 0; i < n; ++i) msgs[i] = { r[i].c_str(), c[i].c_str() };

    const char * tmpl = llama_model_chat_template(e->model, nullptr);
    std::vector<char> buf(total * 2 + 1024);
    int len = llama_chat_apply_template(tmpl, msgs.data(), msgs.size(), true, buf.data(), (int32_t) buf.size());
    if (len < 0 && tmpl) {
        tmpl = "chatml";
        len = llama_chat_apply_template(tmpl, msgs.data(), msgs.size(), true, buf.data(), (int32_t) buf.size());
    }
    if (len > (int) buf.size()) {
        buf.resize((size_t) len + 1);
        len = llama_chat_apply_template(tmpl, msgs.data(), msgs.size(), true, buf.data(), (int32_t) buf.size());
    }
    if (len < 0) return nullptr;
    return toBytes(env, std::string(buf.data(), (size_t) len));
}

// Runs generation. Calls callback.onToken(byte[]) for each UTF-8-complete chunk;
// returns 0 on success, negative on error (-1 prompt too long, -2 decode failure).
JNIEXPORT jint JNICALL
Java_com_localforge_app_engine_LlamaEngine_nativeGenerate(JNIEnv * env, jclass, jlong handle, jbyteArray jprompt,
                                                          jint maxTokens, jfloat temperature, jobject callback) {
    auto * e = (Engine *) handle;
    if (!e) return -3;
    e->stop = false;

    jclass cbClass = env->GetObjectClass(callback);
    jmethodID onToken = env->GetMethodID(cbClass, "onToken", "([B)Z");
    if (!onToken) return -4;

    std::string prompt = jbytes(env, jprompt);

    int n_prompt = -llama_tokenize(e->vocab, prompt.c_str(), (int32_t) prompt.size(), nullptr, 0, false, true);
    std::vector<llama_token> tokens((size_t) n_prompt);
    if (llama_tokenize(e->vocab, prompt.c_str(), (int32_t) prompt.size(), tokens.data(), n_prompt, false, true) < 0) {
        return -2;
    }
    // Some templates don't write the BOS token themselves; add it if the model wants one.
    if (llama_vocab_get_add_bos(e->vocab) && (tokens.empty() || tokens[0] != llama_vocab_bos(e->vocab))) {
        tokens.insert(tokens.begin(), llama_vocab_bos(e->vocab));
        ++n_prompt;
    }
    const int n_ctx = (int) llama_n_ctx(e->ctx);
    if (n_prompt >= n_ctx - 8) return -1;

    // Reuse whatever part of the conversation is already in the cache.
    llama_memory_t mem = llama_get_memory(e->ctx);
    size_t common = 0;
    while (common < e->cached.size() && common < tokens.size() && e->cached[common] == tokens[common]) ++common;
    if (common == tokens.size() && common > 0) --common; // must decode at least one token
    if (common == 0 || !llama_memory_seq_rm(mem, 0, (llama_pos) common, -1)) {
        llama_memory_clear(mem, true);
        common = 0;
    }
    e->cached.assign(tokens.begin(), tokens.begin() + (long) common);
    LOGI("prompt %d tokens, reusing %zu from cache", n_prompt, common);

    // Feed the new part of the prompt in chunks no larger than n_batch.
    const int chunk = 512;
    for (int i = (int) common; i < n_prompt; i += chunk) {
        int n = std::min(chunk, n_prompt - i);
        llama_batch b = llama_batch_get_one(tokens.data() + i, n);
        if (llama_decode(e->ctx, b) != 0) {
            llama_memory_clear(mem, true);
            e->cached.clear();
            return -2;
        }
        e->cached.insert(e->cached.end(), tokens.begin() + i, tokens.begin() + i + n);
        if (e->stop) return 0;
    }

    llama_sampler_chain_params sp = llama_sampler_chain_default_params();
    llama_sampler * smpl = llama_sampler_chain_init(sp);
    if (temperature <= 0.0f) {
        llama_sampler_chain_add(smpl, llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(smpl, llama_sampler_init_top_k(40));
        llama_sampler_chain_add(smpl, llama_sampler_init_top_p(0.95f, 1));
        llama_sampler_chain_add(smpl, llama_sampler_init_min_p(0.05f, 1));
        llama_sampler_chain_add(smpl, llama_sampler_init_temp(temperature));
        llama_sampler_chain_add(smpl, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    }

    std::string pending;
    int produced = 0;
    int rc = 0;
    int n_past = n_prompt;
    while (produced < maxTokens && !e->stop) {
        if (n_past >= n_ctx - 1) break;
        llama_token tok = llama_sampler_sample(smpl, e->ctx, -1);
        if (llama_vocab_is_eog(e->vocab, tok)) break;
        pending += tokenToPiece(e->vocab, tok);
        size_t tail = incompleteUtf8Tail(pending);
        if (pending.size() > tail) {
            std::string ready = pending.substr(0, pending.size() - tail);
            pending.erase(0, pending.size() - tail);
            jbyteArray arr = toBytes(env, ready);
            jboolean keepGoing = env->CallBooleanMethod(callback, onToken, arr);
            env->DeleteLocalRef(arr);
            if (env->ExceptionCheck()) { env->ExceptionClear(); break; }
            if (!keepGoing) break;
        }
        llama_batch b = llama_batch_get_one(&tok, 1);
        if (llama_decode(e->ctx, b) != 0) {
            llama_memory_clear(mem, true);
            e->cached.clear();
            rc = -2;
            break;
        }
        e->cached.push_back(tok);
        ++produced;
        ++n_past;
    }
    llama_sampler_free(smpl);
    return rc;
}

} // extern "C"
