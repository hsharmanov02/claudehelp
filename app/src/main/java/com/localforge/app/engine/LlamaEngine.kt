package com.localforge.app.engine

import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.withContext
import java.util.concurrent.Executors

/** Called from native code for every chunk of generated text. Return false to stop. */
interface TokenCallback {
    fun onToken(bytes: ByteArray): Boolean
}

/** Thin JNI layer over llama.cpp (see src/main/cpp/localforge_jni.cpp). */
object LlamaEngine {
    init {
        System.loadLibrary("localforge")
    }

    @Volatile private var devices = -1

    /** Loads the CPU backend best suited to this phone's chip. Safe to call repeatedly. */
    @Synchronized
    fun ensureInit(nativeLibDir: String): Int {
        if (devices < 0) devices = nativeInit(nativeLibDir)
        return devices
    }

    @JvmStatic external fun nativeInit(nativeLibDir: String): Int
    @JvmStatic external fun nativeLoad(path: String, nCtx: Int, nThreads: Int): Long
    @JvmStatic external fun nativeFree(handle: Long)
    @JvmStatic external fun nativeStop(handle: Long)
    @JvmStatic external fun nativeCountTokens(handle: Long, text: ByteArray): Int
    @JvmStatic external fun nativeCtxSize(handle: Long): Int
    @JvmStatic external fun nativeFormatChat(handle: Long, roles: Array<String>, contents: Array<ByteArray>): ByteArray?
    @JvmStatic external fun nativeGenerate(
        handle: Long, prompt: ByteArray, maxTokens: Int, temperature: Float, callback: TokenCallback
    ): Int
}

data class ChatMessage(val role: String, val content: String)

/**
 * Real UTF-8 for the engine. A half emoji (a lone surrogate, e.g. page text cut in the middle of one)
 * becomes a plain "?" instead of bytes the tokenizer can't read.
 */
fun utf8(s: String): ByteArray {
    val sb = StringBuilder(s.length)
    var i = 0
    while (i < s.length) {
        val c = s[i]
        if (Character.isHighSurrogate(c) && i + 1 < s.length && Character.isLowSurrogate(s[i + 1])) {
            sb.append(c).append(s[i + 1]); i += 2; continue
        }
        sb.append(if (Character.isSurrogate(c)) '?' else c)
        i++
    }
    return sb.toString().toByteArray(Charsets.UTF_8)
}

/** Owns the single loaded model; all native calls run on one background thread. */
object Engine {
    private val dispatcher = Executors.newSingleThreadExecutor().asCoroutineDispatcher()

    @Volatile private var handle: Long = 0L
    @Volatile var loadedPath: String? = null
        private set

    val isLoaded: Boolean get() = handle != 0L

    /** Guards the handle against being freed while the UI thread counts tokens. */
    private val lock = Any()
    private val tokenCache = object : LinkedHashMap<String, Int>(256, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Int>?) = size > 400
    }

    /** Context size of the loaded model (0 if none). */
    fun contextSize(): Int = synchronized(lock) { if (handle != 0L) LlamaEngine.nativeCtxSize(handle) else 0 }

    /** Exact token count of [text] for the loaded model, or an estimate if none is loaded. */
    fun countTokens(text: String): Int = synchronized(lock) {
        if (text.isEmpty()) return 0
        tokenCache[text]?.let { return it }
        val n = if (handle != 0L) LlamaEngine.nativeCountTokens(handle, utf8(text)) else -1
        val out = if (n >= 0) n else (text.length / 3.2).toInt() + 1
        if (n >= 0) tokenCache[text] = out
        out
    }

    /** Speed of the last reply in tokens per second (approximate). */
    @Volatile var lastTokensPerSecond: Double = 0.0
        private set

    suspend fun load(path: String, nCtx: Int, threads: Int, nativeLibDir: String): Boolean = withContext(dispatcher) {
        if (LlamaEngine.ensureInit(nativeLibDir) <= 0) {
            throw IllegalStateException("No CPU backend could be loaded on this phone.")
        }
        if (handle != 0L) {
            synchronized(lock) {
                LlamaEngine.nativeFree(handle)
                handle = 0L
                loadedPath = null
                tokenCache.clear()
            }
        }
        val h = LlamaEngine.nativeLoad(path, nCtx, threads)
        if (h != 0L) synchronized(lock) {
            handle = h
            loadedPath = path
        }
        h != 0L
    }

    suspend fun unload() {
        stop()
        withContext(dispatcher) {
            synchronized(lock) {
                if (handle != 0L) LlamaEngine.nativeFree(handle)
                handle = 0L
                loadedPath = null
                tokenCache.clear()
            }
        }
    }

    fun stop() {
        val h = handle
        if (h != 0L) LlamaEngine.nativeStop(h)
    }

    /**
     * Generates a reply for [messages]. [onText] receives each new piece of text.
     * Returns the full reply, or throws with a readable message.
     */
    suspend fun chat(
        messages: List<ChatMessage>,
        maxTokens: Int,
        temperature: Float,
        noThink: Boolean = false,
        prefill: String = "",
        onText: (String) -> Unit
    ): String = withContext(dispatcher) {
        val h = handle
        if (h == 0L) throw IllegalStateException("No model loaded. Load one on the Home tab.")
        val formatted = LlamaEngine.nativeFormatChat(
            h,
            messages.map { it.role }.toTypedArray(),
            messages.map { utf8(it.content) }.toTypedArray()
        ) ?: throw IllegalStateException("This model's chat format isn't supported.")
        var prompt = formatted
        // Thinking models (Qwen3 and similar) can "think" for thousands of tokens before answering. With thinking
        // off, the reply starts after an empty think block, the same as their own "enable_thinking=false".
        val text = String(formatted, Charsets.UTF_8)
        if (noThink && text.endsWith("<|im_start|>assistant\n")) prompt = utf8(text + "<think>\n\n</think>\n\n")
        // The start of the reply is already written (e.g. its thinking, closed off): the model carries on from there.
        if (prefill.isNotEmpty()) prompt = utf8(String(prompt, Charsets.UTF_8) + prefill)
        val out = StringBuilder()
        var pieces = 0
        var firstAt = 0L
        val rc = LlamaEngine.nativeGenerate(h, prompt, maxTokens, temperature, object : TokenCallback {
            override fun onToken(bytes: ByteArray): Boolean {
                if (pieces == 0) firstAt = System.nanoTime()
                pieces++
                val piece = String(bytes, Charsets.UTF_8)
                out.append(piece)
                onText(piece)
                return true
            }
        })
        val secs = (System.nanoTime() - firstAt) / 1e9
        lastTokensPerSecond = if (pieces > 1 && secs > 0) (pieces - 1) / secs else 0.0
        when (rc) {
            -1 -> throw IllegalStateException("The conversation is too long for the context size. Clear the chat or raise context in settings.")
            -2 -> throw IllegalStateException("The model failed while generating (out of memory?). Try a smaller model or context.")
            else -> {}
        }
        out.toString()
    }
}
