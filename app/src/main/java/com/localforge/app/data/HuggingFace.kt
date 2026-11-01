package com.localforge.app.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

data class HfModel(val id: String, val downloads: Long, val likes: Long)
data class HfFile(val repo: String, val path: String, val size: Long) {
    val fileName: String get() = path.substringAfterLast('/')
    val quant: String
        get() = Regex("(IQ\\d_\\w+|Q\\d_K_[SML]|Q\\d_K|Q\\d_\\d|Q\\d|F16|BF16|F32)", RegexOption.IGNORE_CASE)
            .find(fileName)?.value?.uppercase() ?: "GGUF"
}

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

    suspend fun search(query: String, token: String?): List<HfModel> = withContext(Dispatchers.IO) {
        val q = URLEncoder.encode(query.trim(), "UTF-8")
        val json = JSONArray(get("$BASE/api/models?search=$q&filter=gguf&sort=downloads&direction=-1&limit=40", token))
        (0 until json.length()).map {
            val o = json.getJSONObject(it)
            HfModel(o.getString("id"), o.optLong("downloads"), o.optLong("likes"))
        }
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
                    "file" -> if (path.endsWith(".gguf", true) && !path.contains("mmproj", true)) {
                        val size = o.optJSONObject("lfs")?.optLong("size") ?: o.optLong("size")
                        result += HfFile(repo, path, size)
                    }
                }
            }
        }
        walk("", 0)
        result.sortedBy { it.size }
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
