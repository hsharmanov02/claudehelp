package com.localforge.app.engine

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * Your own model on a rented GPU (e.g. remote/modal_engine.py on Modal): a llama.cpp server reached over HTTPS
 * with your key. Same chat format and streaming as on the phone, just a much bigger GPU.
 */
object RemoteEngine {
    @Volatile var url: String = ""
        private set
    @Volatile private var key: String = ""
    @Volatile var model: String = ""
        private set
    @Volatile var ctx: Int = 0
        private set
    /** Set up and used for chats (the server itself may be asleep; it's woken on the next request). */
    @Volatile var ready = false
    @Volatile private var conn: HttpURLConnection? = null
    @Volatile private var stopped = false
    @Volatile private var lastOk = 0L
    @Volatile var lastTokensPerSecond = 0.0
        private set
    /** What the remote engine is doing right now ("Waking the GPU…"), for the screen. */
    @Volatile var phase: String? = null
        private set

    /** The server has been quiet long enough that it has probably scaled to zero. */
    fun asleep(): Boolean = System.currentTimeMillis() - lastOk > 90_000

    fun configure(u: String, k: String, savedModel: String = "", savedCtx: Int = 0) {
        url = u.trim().trimEnd('/')
        key = k.trim()
        model = savedModel
        ctx = savedCtx
    }

    private fun open(path: String, method: String = "GET", timeoutMs: Int = 30_000): HttpURLConnection =
        (URL(url + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 30_000
            readTimeout = timeoutMs
            setRequestProperty("Authorization", "Bearer $key")
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("User-Agent", "LocalForge/1.0")
        }

    private fun readAll(c: HttpURLConnection): String =
        (if (c.responseCode in 200..299) c.inputStream else (c.errorStream ?: c.inputStream)).bufferedReader().use { it.readText() }

    /**
     * Waits until the server answers /health: a GPU that scaled to zero takes a minute or two to start and load
     * the model. Gives up after [maxMs].
     */
    suspend fun waitReady(maxMs: Long = 6 * 60_000L) = withContext(Dispatchers.IO) {
        val end = System.currentTimeMillis() + maxMs
        var last = ""
        phase = "Waking the remote GPU…"
        try {
            while (System.currentTimeMillis() < end) {
                if (stopped) throw IllegalStateException("Stopped.")
                try {
                    val c = open("/health", timeoutMs = 60_000)
                    val code = c.responseCode
                    c.disconnect()
                    if (code == 200) { lastOk = System.currentTimeMillis(); return@withContext }
                    last = "HTTP $code"
                    if (code == 404) throw IllegalStateException("Nothing at $url (HTTP 404). Check the address.")
                    if (code == 503) phase = "The remote GPU is loading the model…"
                } catch (e: IOException) {
                    last = e.message ?: e.javaClass.simpleName
                }
                delay(4000)
            }
            throw IllegalStateException("The remote engine didn't wake up ($last). Check it's deployed and has credit left.")
        } finally {
            phase = null
        }
    }

    /** Checks the address and key, and reads the model name and context size. */
    suspend fun connect(): String = withContext(Dispatchers.IO) {
        stopped = false
        if (!url.startsWith("https://") && !url.startsWith("http://")) throw IllegalStateException("The address must start with https://")
        waitReady()
        val c = open("/v1/models")
        val code = c.responseCode
        val body = readAll(c)
        c.disconnect()
        if (code == 401) throw IllegalStateException("Wrong key: the remote engine refused it.")
        if (code !in 200..299) throw IllegalStateException("The remote engine answered HTTP $code.")
        model = JSONObject(body).optJSONArray("data")?.optJSONObject(0)?.optString("id").orEmpty().ifBlank { "remote model" }
        ctx = try {
            val p = open("/props")
            val o = JSONObject(readAll(p)); p.disconnect()
            o.optJSONObject("default_generation_settings")?.optInt("n_ctx", 0)?.takeIf { it > 0 } ?: o.optInt("n_ctx", 8192)
        } catch (_: Exception) { 8192 }
        ready = true
        model
    }

    /** Forgets an earlier Stop, before a new wake-up. */
    fun clearStop() { stopped = false }

    fun stop() {
        stopped = true
        try { conn?.disconnect() } catch (_: Exception) {}
    }

    /** Streams a reply. [prefill] is the start of the reply that's already written (the model carries on). */
    suspend fun chat(
        messages: List<ChatMessage>, maxTokens: Int, temperature: Float, noThink: Boolean, prefill: String,
        onText: (String) -> Unit
    ): String = withContext(Dispatchers.IO) {
        stopped = false
        // A server that has been quiet for a while has probably scaled to zero: wake it first.
        if (asleep()) waitReady()
        val msgs = JSONArray()
        messages.forEach { msgs.put(JSONObject().put("role", it.role).put("content", it.content)) }
        if (prefill.isNotEmpty()) msgs.put(JSONObject().put("role", "assistant").put("content", prefill))
        val body = JSONObject().put("messages", msgs).put("stream", true).put("max_tokens", maxTokens)
            .put("temperature", temperature.toDouble())
        if (noThink) body.put("chat_template_kwargs", JSONObject().put("enable_thinking", false))
        val c = open("/v1/chat/completions", "POST", timeoutMs = 5 * 60_000)
        conn = c
        val out = StringBuilder()
        try {
            c.doOutput = true
            c.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = c.responseCode
            if (code == 401) throw IllegalStateException("Wrong key: the remote engine refused it.")
            if (code == 400 && readAll(c).contains("context", ignoreCase = true)) {
                throw IllegalStateException("The conversation is too long for the remote model's context. Clear the chat.")
            }
            if (code !in 200..299) throw IllegalStateException("The remote engine answered HTTP $code.")
            c.inputStream.bufferedReader(Charsets.UTF_8).useLines { lines ->
                for (raw in lines) {
                    if (stopped) break
                    val line = raw.trim()
                    if (!line.startsWith("data:")) continue
                    val data = line.removePrefix("data:").trim()
                    if (data == "[DONE]") break
                    val o = try { JSONObject(data) } catch (_: Exception) { continue }
                    o.optJSONArray("choices")?.optJSONObject(0)?.optJSONObject("delta")?.let { d ->
                        if (d.has("content") && !d.isNull("content")) {
                            val piece = d.optString("content")
                            if (piece.isNotEmpty()) { out.append(piece); onText(piece) }
                        }
                    }
                    o.optJSONObject("timings")?.let { t -> lastTokensPerSecond = t.optDouble("predicted_per_second", lastTokensPerSecond) }
                }
            }
            lastOk = System.currentTimeMillis()
        } catch (e: IOException) {
            if (!stopped) throw IllegalStateException("Lost the connection to the remote engine (${e.message}).")
        } finally {
            conn = null
            c.disconnect()
        }
        out.toString()
    }
}
