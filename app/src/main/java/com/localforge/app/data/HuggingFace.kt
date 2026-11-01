package com.localforge.app.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import org.json.JSONArray
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

data class HfModel(val id: String, val downloads: Long, val likes: Long)
data class HfFile(val repo: String, val path: String, val size: Long) {
    val fileName: String get() = path.substringAfterLast('/')
    val quant: String
        get() = Regex("(IQ\\d_\\w+?|Q\\d_K_[SML]|Q\\d_K|Q\\d_\\d|F16|BF16|F32)(?=[.-])", RegexOption.IGNORE_CASE)
            .find(fileName)?.value?.uppercase() ?: "GGUF"
}

data class Recommendation(val file: HfFile, val downloads: Long, val paramsB: Double, val reason: String)

/** Public Hugging Face Hub API — no token needed for public models. */
object HuggingFace {
    private const val BASE = "https://huggingface.co"

    private fun get(url: String, token: String?): String {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 15000
        conn.readTimeout = 20000
        conn.setRequestProperty("User-Agent", "LocalForge/1.0")
        if (!token.isNullOrBlank()) conn.setRequestProperty("Authorization", "Bearer $token")
        try {
            val code = conn.responseCode
            if (code !in 200..299) throw IllegalStateException("Hugging Face returned HTTP $code")
            return conn.inputStream.bufferedReader().use { it.readText() }
        } finally {
            conn.disconnect()
        }
    }

    private fun rawSearch(query: String, token: String?, limit: Int = 40): List<HfModel> {
        val q = URLEncoder.encode(query.trim(), "UTF-8")
        val json = JSONArray(get("$BASE/api/models?search=$q&filter=gguf&sort=downloads&direction=-1&limit=$limit", token))
        return (0 until json.length()).map {
            val o = json.getJSONObject(it)
            HfModel(o.getString("id"), o.optLong("downloads"), o.optLong("likes"))
        }
    }

    /** Searches only uncensored / abliterated GGUF repos. */
    suspend fun search(query: String, token: String?): List<HfModel> = withContext(Dispatchers.IO) {
        val base = query.trim()
        val queries = if (Uncensored.matches(base)) listOf(base)
        else if (base.isEmpty()) listOf("uncensored", "abliterated")
        else listOf("$base uncensored", "$base abliterated")
        queries.flatMap { runCatching { rawSearch(it, token) }.getOrDefault(emptyList()) }
            .filter { Uncensored.matches(it.id) }
            .distinctBy { it.id }
            .sortedByDescending { it.downloads }
    }

    suspend fun ggufFiles(repo: String, token: String?): List<HfFile> = withContext(Dispatchers.IO) {
        val result = mutableListOf<HfFile>()
        fun walk(dir: String, depth: Int) {
            val suffix = if (dir.isEmpty()) "" else "/" + dir.split('/').joinToString("/") { encode(it) }
            val arr = JSONArray(get("$BASE/api/models/$repo/tree/main$suffix", token))
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                val path = o.getString("path")
                when (o.optString("type")) {
                    "directory" -> if (depth < 1) walk(path, depth + 1)
                    "file" -> if (path.endsWith(".gguf", true) && !path.contains("mmproj", true) &&
                        !Regex("-\\d{5}-of-\\d{5}").containsMatchIn(path)
                    ) {
                        val size = o.optJSONObject("lfs")?.optLong("size") ?: o.optLong("size")
                        result += HfFile(repo, path, size)
                    }
                }
            }
        }
        walk("", 0)
        result.sortedBy { it.size }
    }

    // Only good-quality 4/5-bit quants. Q4_0 and IQ4_NL get repacked into a faster layout on ARM chips.
    private val quantRank = mapOf(
        "Q4_0" to 1.0, "IQ4_NL" to 0.99, "Q4_K_M" to 0.97, "Q4_K_S" to 0.95, "IQ4_XS" to 0.94, "Q5_K_M" to 0.9
    )
    private val trusted = listOf("bartowski", "mradermacher", "unsloth", "lmstudio-community", "quantfactory", "huihui-ai")
    private val excluded = listOf("vision", "-vl", "mmproj", "embed", "reward", "draft", "audio", "tts", "rerank")

    /** Billions of parameters from the repo name, or null if unclear. MoE names like 30B-A3B count the full size. */
    fun paramsOf(name: String): Double? {
        val all = Regex("(?<![\\d.A-Za-z])(\\d+(?:\\.\\d+)?)\\s*[bB](?![a-zA-Z])").findAll(name)
            .mapNotNull { it.groupValues[1].toDoubleOrNull() }.filter { it in 0.1..1000.0 }.toList()
        return all.maxOrNull()
    }

    /** Rough generation speed on a recent phone: generation is limited by memory bandwidth (~15 GB/s usable). */
    fun estTokensPerSec(fileSize: Long): Double = if (fileSize <= 0) 0.0 else 15e9 / fileSize

    /**
     * Realistic, fast picks for this phone: small uncensored models (≤4.5B) at good 4-bit quants that
     * fit comfortably in memory and run at a usable speed. [coding] boosts coder models.
     */
    suspend fun recommend(device: DeviceInfo, token: String?, coding: Boolean): List<Recommendation> = coroutineScope {
        val maxFile = minOf(device.modelBudget, device.freeStorage - 500L * 1024 * 1024)
        val queries = listOf(
            "1.5B abliterated", "3B abliterated", "4B abliterated", "2B abliterated", "1B abliterated",
            "3B uncensored", "1B uncensored", "4B uncensored",
            "llama 3.2 3b abliterated", "qwen2.5 3b abliterated", "qwen3 4b abliterated", "gemma 3 4b abliterated",
            "qwen2.5 coder 3b abliterated", "qwen2.5 coder 1.5b abliterated", "dolphin 3b"
        )
        val repos = queries.map { q -> async(Dispatchers.IO) { runCatching { rawSearch(q, token, 25) }.getOrDefault(emptyList()) } }
            .awaitAll().flatten()
            .filter { r ->
                val p = paramsOf(r.id)
                Uncensored.matches(r.id) && p != null && p <= 4.5 && excluded.none { r.id.contains(it, true) } &&
                    !Regex("A\\d+(\\.\\d+)?B", RegexOption.IGNORE_CASE).containsMatchIn(r.id)
            }
            .distinctBy { it.id }
            .sortedByDescending { it.downloads }
            .take(20)

        val picks = repos.map { repo ->
            async {
                val files = runCatching { ggufFiles(repo.id, token) }.getOrDefault(emptyList())
                val params = paramsOf(repo.id) ?: return@async null
                val best = files.filter { it.size in 1..maxFile && it.quant in quantRank }
                    .maxByOrNull { quantRank.getValue(it.quant) } ?: return@async null
                val speed = estTokensPerSec(best.size)
                val isCoder = repo.id.contains("coder", true)
                val speedFactor = if (speed >= 8) 1.0 else speed / 8
                val trust = if (trusted.any { repo.id.startsWith(it, true) }) 1.1 else 1.0
                val score = params * quantRank.getValue(best.quant) * speedFactor * trust * (if (coding && isCoder) 1.15 else 1.0)
                Triple(score, repo, Recommendation(best, repo.downloads, params, buildReason(params, best, speed, isCoder)))
            }
        }.awaitAll().filterNotNull()

        picks.sortedWith(compareByDescending<Triple<Double, HfModel, Recommendation>> { it.first }.thenByDescending { it.second.downloads })
            .map { it.third }
            .distinctBy { it.file.repo }
            .take(6)
    }

    private fun buildReason(params: Double, f: HfFile, speed: Double, coder: Boolean): String {
        val p = if (params >= 10) "%.0f".format(params) else "%.1f".format(params)
        val tier = if (speed >= 12) "Fast" else "Balanced"
        return "$tier · ${p}B · ${f.quant} · ~${speed.toInt()} tokens/sec on your phone" + if (coder) " · coding model" else ""
    }

    fun downloadUrl(file: HfFile): String =
        "$BASE/${file.repo}/resolve/main/" + file.path.split('/').joinToString("/") { encode(it) } + "?download=true"

    private fun encode(s: String) = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
}

fun formatBytes(b: Long): String = when {
    b <= 0 -> "?"
    b >= 1L shl 30 -> String.format("%.2f GB", b / (1L shl 30).toDouble())
    b >= 1L shl 20 -> String.format("%.0f MB", b / (1L shl 20).toDouble())
    b >= 1L shl 10 -> String.format("%.0f KB", b / (1L shl 10).toDouble())
    else -> "$b B"
}
