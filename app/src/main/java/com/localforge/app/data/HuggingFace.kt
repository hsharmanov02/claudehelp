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

    // Best quality-per-byte first. Lower quants only when nothing better fits.
    private val quantRank = listOf(
        "Q4_K_M" to 1.0, "Q4_K_S" to 0.97, "IQ4_XS" to 0.96, "Q5_K_M" to 0.99, "Q5_K_S" to 0.98,
        "Q4_0" to 0.93, "IQ4_NL" to 0.95, "Q3_K_L" to 0.82, "Q3_K_M" to 0.8, "IQ3_M" to 0.8,
        "Q3_K_S" to 0.72, "IQ3_XS" to 0.72, "Q2_K" to 0.55
    )

    private fun paramsOf(name: String, fileSize: Long): Double {
        val m = Regex("(?<![\\d.])(\\d+(?:\\.\\d+)?)\\s*[bB](?![a-zA-Z])").findAll(name).lastOrNull()
        val p = m?.groupValues?.get(1)?.toDoubleOrNull()
        if (p != null && p in 0.3..400.0) return p
        return fileSize / 0.6e9 // rough: Q4 ≈ 0.6 GB per billion params
    }

    /**
     * Ranks uncensored models for this phone: biggest model that fits in memory at a good quant wins,
     * popularity breaks ties. [coding] adds coder-focused searches.
     */
    suspend fun recommend(device: DeviceInfo, token: String?, coding: Boolean): List<Recommendation> = coroutineScope {
        val limit = minOf(device.modelBudget, device.freeStorage - 500L * 1024 * 1024)
        val queries = mutableListOf("abliterated", "uncensored", "dolphin", "instruct abliterated")
        if (coding) queries += listOf("coder abliterated", "coder uncensored", "qwen2.5 coder abliterated")
        val repos = withContext(Dispatchers.IO) {
            queries.flatMap { runCatching { rawSearch(it, token, 30) }.getOrDefault(emptyList()) }
        }
            .filter { Uncensored.matches(it.id) && !it.id.contains("vision", true) && !it.id.contains("-VL", true) }
            .distinctBy { it.id }
            .sortedByDescending { it.downloads }
            .take(18)

        val picks = repos.map { repo ->
            async {
                val files = runCatching { ggufFiles(repo.id, token) }.getOrDefault(emptyList())
                val fitting = files.filter { it.size in 1..limit }
                var best: Pair<HfFile, Double>? = null
                for ((q, quality) in quantRank) {
                    val f = fitting.firstOrNull { it.quant == q } ?: continue
                    if (best == null || quality > best.second) best = f to quality
                }
                best?.let { (f, quality) ->
                    val params = paramsOf(repo.id, f.size)
                    val isCoder = repo.id.contains("coder", true) || repo.id.contains("code", true)
                    val score = params * quality * (if (coding && isCoder) 1.25 else 1.0)
                    Triple(score, repo, Recommendation(f, repo.downloads, params, buildReason(params, f, device, isCoder && coding)))
                }
            }
        }.awaitAll().filterNotNull()

        picks.sortedWith(compareByDescending<Triple<Double, HfModel, Recommendation>> { it.first }.thenByDescending { it.second.downloads })
            .map { it.third }
            .distinctBy { it.file.repo }
            .take(6)
    }

    private fun buildReason(params: Double, f: HfFile, d: DeviceInfo, coder: Boolean): String {
        val pct = (f.size * 100 / d.totalRam.coerceAtLeast(1)).toInt()
        val p = if (params >= 10) "%.0f".format(params) else "%.1f".format(params)
        return "~${p}B params · ${f.quant} · uses ~$pct% of your RAM" + if (coder) " · coding model" else ""
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
