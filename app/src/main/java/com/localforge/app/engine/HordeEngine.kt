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
 * AI Horde (aihorde.net): free volunteer GPUs, no account needed (the anonymous key). Big uncensored models, but
 * volunteers run them, so prompts aren't private. Replies come back whole (no streaming): a job is queued, then
 * polled until a worker has written it.
 */
object HordeEngine {
    private const val API = "https://aihorde.net/api/v2"
    const val ANON_KEY = "0000000000"

    data class HordeModel(val name: String, val workers: Int, val queued: Double, val eta: Int, val ctx: Int, val maxReply: Int) {
        val uncensored: Boolean get() = VERIFIED.any { name.lowercase().contains(it) }
        val short: String get() = name.substringAfterLast('/')
        /** Size in billions of parameters, read from the name ("Cydonia-24B", "26B-A4B" -> 26), or 0 if it doesn't say. */
        val billions: Double get() = Regex("(?<![a-z0-9])e?(\\d+(?:\\.\\d+)?)b(?![a-z])", RegexOption.IGNORE_CASE)
            .findAll(name.substringAfterLast('/')).mapNotNull { it.groupValues[1].toDoubleOrNull() }.maxOrNull() ?: 0.0
    }

    /**
     * Only models that passed our refusal tests (remote/test_horde.py: a swearing roast, an explicit adult scene, dark
     * jokes, lockpicking and insults, all answered, with the app's own instructions). Names alone lie: several
     * "heretic" / "uncensored" models on the Horde still refuse. Matched on the model name, so new quantizations count.
     */
    val VERIFIED = listOf("g4-meromero-26b")

    /** Only big models: small ones can't follow the app's tools and instructions well. */
    const val MIN_BILLIONS = 20.0

    @Volatile var model: String = ""
        private set
    @Volatile var ctx: Int = 4096
        private set
    @Volatile var maxReply: Int = 512
        private set
    @Volatile private var key: String = ANON_KEY
    @Volatile var ready = false
    @Volatile var lastTokensPerSecond = 0.0
        private set
    @Volatile private var stopped = false
    @Volatile private var jobId: String? = null
    /** Queue updates for the screen ("in the queue, position 3…"). */
    @Volatile var onStatus: (String?) -> Unit = {}

    fun configure(m: String, k: String, savedCtx: Int = 4096, savedReply: Int = 512) {
        model = m; key = k.trim().ifBlank { ANON_KEY }; ctx = savedCtx; maxReply = savedReply
    }

    private fun request(path: String, body: JSONObject? = null, method: String = if (body != null) "POST" else "GET"): Pair<Int, String> {
        val c = (URL(API + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 30_000
            readTimeout = 60_000
            setRequestProperty("apikey", key)
            setRequestProperty("Client-Agent", "LocalForge:1.0:github.com/hsharmanov02/claudehelp")
            setRequestProperty("User-Agent", "LocalForge/1.0")
            setRequestProperty("Content-Type", "application/json")
        }
        try {
            if (body != null) {
                c.doOutput = true
                c.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = c.responseCode
            val text = (if (code in 200..299) c.inputStream else (c.errorStream ?: c.inputStream)).bufferedReader().use { it.readText() }
            return code to text
        } finally {
            c.disconnect()
        }
    }

    /** Text models online now, with the biggest context/reply their workers allow; uncensored and busiest first. */
    suspend fun listModels(): List<HordeModel> = withContext(Dispatchers.IO) {
        val (code, body) = request("/status/models?type=text")
        if (code !in 200..299) throw IllegalStateException("AI Horde answered HTTP $code.")
        val limits = HashMap<String, Pair<Int, Int>>()
        try {
            val (wc, wb) = request("/workers?type=text")
            if (wc in 200..299) {
                val ws = JSONArray(wb)
                for (i in 0 until ws.length()) {
                    val w = ws.getJSONObject(i)
                    val ms = w.optJSONArray("models") ?: continue
                    for (j in 0 until ms.length()) {
                        val n = ms.getString(j)
                        val old = limits[n] ?: (0 to 0)
                        limits[n] = maxOf(old.first, w.optInt("max_context_length", 0)) to maxOf(old.second, w.optInt("max_length", 0))
                    }
                }
            }
        } catch (_: Exception) {}
        val arr = JSONArray(body)
        (0 until arr.length()).map { arr.getJSONObject(it) }.map {
            val n = it.getString("name")
            val (c, r) = limits[n] ?: (4096 to 512)
            HordeModel(n, it.optInt("count"), it.optDouble("queued"), it.optInt("eta"), c.takeIf { v -> v > 0 } ?: 4096, r.takeIf { v -> v > 0 } ?: 512)
        }.filter { it.workers > 0 && it.uncensored && it.billions >= MIN_BILLIONS && it.ctx >= 4096 }
            .sortedWith(compareByDescending<HordeModel> { it.workers }.thenBy { it.eta })
    }

    /** Uses [m] for every chat from now on. */
    fun use(m: HordeModel, k: String) {
        require(m.uncensored && m.billions >= MIN_BILLIONS) { "LocalForge only uses uncensored or abliterated models of 20B or more." }
        configure(m.name, k, minOf(m.ctx, 32768), minOf(m.maxReply, 4096))
        ready = true
    }

    fun stop() {
        stopped = true
        val id = jobId ?: return
        Thread { try { request("/generate/text/status/$id", method = "DELETE") } catch (_: Exception) {} }.start()
    }

    /** The chat as the raw text the model was trained on (Horde takes a plain prompt). */
    fun format(messages: List<ChatMessage>, noThink: Boolean): Pair<String, List<String>> {
        val n = model.lowercase()
        val sb = StringBuilder()
        return when {
            "gemma" in n -> {
                // Gemma has no system role: the system text goes before the first user message.
                var system = ""
                messages.forEach { m ->
                    when (m.role) {
                        "system" -> system += m.content + "\n\n"
                        "assistant" -> sb.append("<start_of_turn>model\n").append(m.content).append("<end_of_turn>\n")
                        else -> { sb.append("<start_of_turn>user\n").append(system).append(m.content).append("<end_of_turn>\n"); system = "" }
                    }
                }
                sb.append("<start_of_turn>model\n")
                sb.toString() to listOf("<end_of_turn>", "<start_of_turn>")
            }
            "l3" in n || "llama-3" in n || "llama3" in n || "stheno" in n -> {
                sb.append("<|begin_of_text|>")
                messages.forEach { m -> sb.append("<|start_header_id|>${m.role}<|end_header_id|>\n\n").append(m.content).append("<|eot_id|>") }
                sb.append("<|start_header_id|>assistant<|end_header_id|>\n\n")
                sb.toString() to listOf("<|eot_id|>", "<|start_header_id|>")
            }
            else -> {
                messages.forEach { m -> sb.append("<|im_start|>${m.role}\n").append(m.content).append("<|im_end|>\n") }
                sb.append("<|im_start|>assistant\n")
                if (noThink && "qwen3" in n) sb.append("<think>\n\n</think>\n\n")
                sb.toString() to listOf("<|im_end|>", "<|im_start|>")
            }
        }
    }

    /** Sends the chat as one Horde job and waits for the reply. [onWait] gets queue updates for the screen. */
    suspend fun chat(
        messages: List<ChatMessage>, maxTokens: Int, temperature: Float, noThink: Boolean, prefill: String,
        onText: (String) -> Unit
    ): String = withContext(Dispatchers.IO) {
        stopped = false
        val (prompt0, stops) = format(messages, noThink)
        val prompt = prompt0 + prefill
        val params = JSONObject()
            .put("max_length", maxTokens.coerceIn(16, maxReply))
            .put("max_context_length", ctx)
            .put("temperature", temperature.toDouble().coerceAtLeast(0.01))
            .put("stop_sequence", JSONArray(stops))
        val body = JSONObject().put("prompt", prompt).put("params", params).put("models", JSONArray().put(model))
        val t0 = System.currentTimeMillis()
        val (code, resp) = try { request("/generate/text/async", body) } catch (e: IOException) {
            throw IllegalStateException("Couldn't reach AI Horde (${e.message}).")
        }
        if (code == 401) throw IllegalStateException("AI Horde refused the key. Clear it to use the free anonymous key.")
        if (code == 429) throw IllegalStateException("AI Horde says too many requests right now. Try again in a minute.")
        if (code !in 200..299) throw IllegalStateException("AI Horde answered HTTP $code: ${resp.take(200)}")
        val id = JSONObject(resp).getString("id")
        jobId = id
        try {
            var polls = 0
            var finalBody = ""
            while (true) {
                if (stopped) return@withContext ""
                delay(if (polls++ < 5) 2000 else 3000)
                val (cc, cb) = try { request("/generate/text/status/$id") } catch (_: IOException) { continue }
                if (cc == 404) throw IllegalStateException("AI Horde dropped the request (it took too long). Try again or pick a busier model.")
                if (cc !in 200..299) continue
                val c = JSONObject(cb)
                if (c.optBoolean("faulted")) throw IllegalStateException("The AI Horde worker failed on this request. Try again.")
                if (!c.optBoolean("is_possible", true)) {
                    throw IllegalStateException("No AI Horde worker for $model can take a prompt this long right now. Clear the chat or pick another model.")
                }
                if (c.optBoolean("done")) { finalBody = cb; break }
                val pos = c.optInt("queue_position", 0)
                val wait = c.optInt("wait_time", 0)
                onStatus(if (c.optInt("processing") > 0) "AI Horde: a volunteer GPU is writing the reply…"
                       else "AI Horde: in the queue (position $pos, about ${wait}s)…")
            }
            val gens = JSONObject(finalBody).optJSONArray("generations")
            var text = gens?.optJSONObject(0)?.optString("text").orEmpty()
            stops.forEach { s -> val i = text.indexOf(s); if (i >= 0) text = text.substring(0, i) }
            val secs = (System.currentTimeMillis() - t0) / 1000.0
            lastTokensPerSecond = if (secs > 0) (text.length / 3.5) / secs else 0.0
            if (text.isNotEmpty()) onText(text)
            text
        } finally {
            jobId = null
            onStatus(null)
        }
    }
}
