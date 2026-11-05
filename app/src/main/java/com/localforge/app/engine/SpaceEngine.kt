package com.localforge.app.engine

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * Huihui Gemma 4 31B abliterated v2 on public Hugging Face Spaces (free GPUs, no account), reached through
 * Gradio's plain HTTP API. It passed every refusal test (remote/test_space.py, remote/probe_free.py). Hugging Face
 * limits free GPU runs per person: without an account it's a few replies at a time; a free Hugging Face token
 * (the same one the Models tab uses) gives more. Two copies of the Space are tried in turn.
 */
object SpaceEngine {
    const val MODEL = "Huihui Gemma 4 31B abliterated"
    val HOSTS = listOf(
        "https://mxderncultxre-gemma4-31b-uncensored-assistant.hf.space",
        "https://jickman125-gemma4-31b-uncensored-assistant.hf.space",
    )
    const val CTX = 32768

    @Volatile var ready = false
    @Volatile var token: String = ""
    @Volatile var lastTokensPerSecond = 0.0
        private set
    @Volatile private var stopped = false
    @Volatile private var conn: HttpURLConnection? = null
    @Volatile private var hostIndex = 0
    /** Queue / quota news for the screen. */
    @Volatile var onStatus: (String?) -> Unit = {}
    private val params = HashMap<String, List<Pair<String, Any?>>>()

    private fun open(url: String, body: JSONObject? = null, readMs: Int = 60_000): HttpURLConnection =
        (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = if (body != null) "POST" else "GET"
            connectTimeout = 30_000
            readTimeout = readMs
            setRequestProperty("User-Agent", "LocalForge/1.0")
            setRequestProperty("Content-Type", "application/json")
            if (token.isNotBlank()) setRequestProperty("Authorization", "Bearer ${token.trim()}")
            if (body != null) {
                doOutput = true
                outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
        }

    private fun text(c: HttpURLConnection): String =
        (if (c.responseCode in 200..299) c.inputStream else (c.errorStream ?: c.inputStream)).bufferedReader().use { it.readText() }

    /** The /chat endpoint's parameter names and defaults, in order (read once per host). */
    private fun paramsFor(host: String): List<Pair<String, Any?>> = params.getOrPut(host) {
        val c = open("$host/gradio_api/info")
        val code = c.responseCode
        val body = text(c); c.disconnect()
        if (code !in 200..299) throw IOException("HTTP $code")
        val arr = JSONObject(body).getJSONObject("named_endpoints").getJSONObject("/chat").getJSONArray("parameters")
        (0 until arr.length()).map { arr.getJSONObject(it) }.map { p ->
            p.getString("parameter_name") to (if (p.has("parameter_default") && !p.isNull("parameter_default")) p.get("parameter_default") else null)
        }
    }

    /** Checks a Space answers (it may need a minute to wake up). */
    suspend fun connect(): Boolean = withContext(Dispatchers.IO) {
        var last: Exception? = null
        for (i in HOSTS.indices) {
            val host = HOSTS[(hostIndex + i) % HOSTS.size]
            try { paramsFor(host); hostIndex = (hostIndex + i) % HOSTS.size; ready = true; return@withContext true }
            catch (e: Exception) { last = e }
        }
        throw IllegalStateException("Couldn't reach the Hugging Face Space (${last?.message}).")
    }

    fun stop() {
        stopped = true
        try { conn?.disconnect() } catch (_: Exception) {}
    }

    private class Quota(msg: String) : Exception(msg)

    suspend fun chat(
        messages: List<ChatMessage>, maxTokens: Int, temperature: Float, prefill: String, onText: (String) -> Unit
    ): String = withContext(Dispatchers.IO) {
        stopped = false
        val system = messages.filter { it.role == "system" }.joinToString("\n\n") { it.content }
        val turns = messages.filter { it.role != "system" }
        val lastUser = turns.indexOfLast { it.role == "user" }
        val history = JSONArray()
        turns.forEachIndexed { i, m ->
            if (i != lastUser) history.put(JSONObject().put("role", if (m.role == "assistant") "assistant" else "user").put("content", m.content))
        }
        var userText = if (lastUser >= 0) turns[lastUser].content else ""
        // Gradio chat apps can't continue a half-written reply: ask for it to carry on instead.
        if (prefill.isNotBlank()) userText += "\n\n(Your reply so far, continue it directly:)\n$prefill"
        val values = mapOf<String, Any?>(
            "text" to userText, "files" to null, "history" to history, "thinking" to false,
            "max_new_tokens" to maxTokens.coerceIn(64, 4096), "temperature" to temperature.toDouble().coerceAtLeast(0.05),
            "system_prompt" to system.ifBlank { null },
        )
        var lastError = ""
        for (attempt in HOSTS.indices) {
            if (stopped) return@withContext ""
            val host = HOSTS[(hostIndex + attempt) % HOSTS.size]
            try {
                val out = runOn(host, values, onText)
                hostIndex = (hostIndex + attempt) % HOSTS.size
                return@withContext out
            } catch (e: Quota) {
                lastError = e.message ?: ""
                onStatus("This copy of the Space hit Hugging Face's free GPU limit, trying the other one…")
            } catch (e: IOException) {
                if (stopped) return@withContext ""
                lastError = e.message ?: e.javaClass.simpleName
            }
        }
        onStatus(null)
        if (lastError.contains("ZeroGPU", true) || lastError.contains("quota", true)) {
            throw IllegalStateException(
                "Hugging Face's free GPU limit is used up for now. Paste a free Hugging Face token in the Models tab " +
                    "for more replies, or wait a while and try again."
            )
        }
        throw IllegalStateException("The Hugging Face Space didn't answer ($lastError).")
    }

    private fun runOn(host: String, values: Map<String, Any?>, onText: (String) -> Unit): String {
        val data = JSONArray()
        paramsFor(host).forEach { (name, def) -> data.put(if (values.containsKey(name)) (values[name] ?: JSONObject.NULL) else (def ?: JSONObject.NULL)) }
        val start = open("$host/gradio_api/call/chat", JSONObject().put("data", data))
        val code = start.responseCode
        val body = text(start); start.disconnect()
        if (code !in 200..299) throw IOException("HTTP $code ${body.take(200)}")
        val id = JSONObject(body).getString("event_id")
        onStatus("Gemma 31B is writing (free Hugging Face GPU)…")
        val c = open("$host/gradio_api/call/chat/$id", readMs = 5 * 60_000)
        conn = c
        val t0 = System.nanoTime()
        var sent = ""
        try {
            var event = ""
            c.inputStream.bufferedReader(Charsets.UTF_8).useLines { lines ->
                for (raw in lines) {
                    if (stopped) break
                    val line = raw.trim()
                    if (line.startsWith("event:")) { event = line.removePrefix("event:").trim(); continue }
                    if (!line.startsWith("data:")) continue
                    val payload = line.removePrefix("data:").trim()
                    if (event == "error") {
                        throw if (payload.contains("ZeroGPU", true) || payload.contains("quota", true)) Quota(payload) else IOException(payload.take(300))
                    }
                    if (event != "generating" && event != "complete") continue
                    val o = try { JSONArray(payload).opt(0) } catch (_: Exception) { null }
                    val obj = o as? JSONObject
                    val err = obj?.optString("error").orEmpty()
                    if (err.isNotBlank()) throw if (err.contains("ZeroGPU", true) || err.contains("quota", true)) Quota(err) else IOException(err.take(300))
                    val content = obj?.optString("content") ?: (o as? String).orEmpty()
                    // Each update carries the whole reply so far: send only what's new.
                    if (content.length > sent.length && content.startsWith(sent)) {
                        onText(content.substring(sent.length)); sent = content
                    } else if (content.isNotEmpty() && !content.startsWith(sent)) {
                        sent = content
                    }
                    if (event == "complete") break
                }
            }
        } finally {
            conn = null
            c.disconnect()
            onStatus(null)
        }
        val secs = (System.nanoTime() - t0) / 1e9
        lastTokensPerSecond = if (secs > 0) (sent.length / 3.5) / secs else 0.0
        return sent
    }
}
