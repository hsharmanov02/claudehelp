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
        nativeInit()
    }

    @JvmStatic external fun nativeInit()
    @JvmStatic external fun nativeLoad(path: String, nCtx: Int, nThreads: Int): Long
    @JvmStatic external fun nativeFree(handle: Long)
    @JvmStatic external fun nativeStop(handle: Long)
    @JvmStatic external fun nativeFormatChat(handle: Long, roles: Array<String>, contents: Array<String>): ByteArray?
    @JvmStatic external fun nativeGenerate(
        handle: Long, prompt: String, maxTokens: Int, temperature: Float, callback: TokenCallback
    ): Int
}

data class ChatMessage(val role: String, val content: String)

/** Owns the single loaded model; all native calls run on one background thread. */
object Engine {
    private val dispatcher = Executors.newSingleThreadExecutor().asCoroutineDispatcher()

    @Volatile private var handle: Long = 0L
    @Volatile var loadedPath: String? = null
        private set

    val isLoaded: Boolean get() = handle != 0L

    suspend fun load(path: String, nCtx: Int, threads: Int): Boolean = withContext(dispatcher) {
        if (handle != 0L) {
            LlamaEngine.nativeFree(handle)
            handle = 0L
            loadedPath = null
        }
        val h = LlamaEngine.nativeLoad(path, nCtx, threads)
        if (h != 0L) {
            handle = h
            loadedPath = path
        }
        h != 0L
    }

    suspend fun unload() {
        stop()
        withContext(dispatcher) {
            if (handle != 0L) LlamaEngine.nativeFree(handle)
            handle = 0L
            loadedPath = null
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
        onText: (String) -> Unit
    ): String = withContext(dispatcher) {
        val h = handle
        if (h == 0L) throw IllegalStateException("No model loaded. Load one on the Home tab.")
        val formatted = LlamaEngine.nativeFormatChat(
            h,
            messages.map { it.role }.toTypedArray(),
            messages.map { it.content }.toTypedArray()
        ) ?: throw IllegalStateException("This model's chat format isn't supported.")
        val prompt = String(formatted, Charsets.UTF_8)
        val out = StringBuilder()
        val rc = LlamaEngine.nativeGenerate(h, prompt, maxTokens, temperature, object : TokenCallback {
            override fun onToken(bytes: ByteArray): Boolean {
                val piece = String(bytes, Charsets.UTF_8)
                out.append(piece)
                onText(piece)
                return true
            }
        })
        when (rc) {
            -1 -> throw IllegalStateException("The conversation is too long for the context size. Clear the chat or raise context in settings.")
            -2 -> throw IllegalStateException("The model failed while generating (out of memory?). Try a smaller model or context.")
            else -> {}
        }
        out.toString()
    }
}
