package com.localforge.app.agent

import android.os.Environment
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Index of every file on the phone's shared storage, so the assistant can find files by name or type
 * without having to explore folder by folder (small models are bad at that).
 */
object FileIndex {
    data class Entry(val path: String, val nameLower: String, val size: Long, val modified: Long, val isDir: Boolean)

    private const val MAX_ENTRIES = 300_000
    private val mutex = Mutex()
    @Volatile private var entries: List<Entry> = emptyList()
    @Volatile var builtAt: Long = 0L
        private set
    val count: Int get() = entries.size
    val isReady: Boolean get() = builtAt > 0

    val root: File get() = Environment.getExternalStorageDirectory()

    /** Builds (or rebuilds) the index. Cheap enough to run every few minutes. */
    suspend fun build() = mutex.withLock {
        withContext(Dispatchers.IO) {
            val out = ArrayList<Entry>(50_000)
            fun walk(dir: File, depth: Int) {
                if (out.size >= MAX_ENTRIES || depth > 14) return
                val kids = dir.listFiles() ?: return
                for (k in kids) {
                    if (out.size >= MAX_ENTRIES) return
                    val name = k.name
                    if (name.startsWith(".")) continue
                    val isDir = k.isDirectory
                    out += Entry(k.absolutePath, name.lowercase(), if (isDir) 0L else k.length(), k.lastModified(), isDir)
                    if (isDir) {
                        // Other apps' private folders are blocked by Android anyway.
                        if (depth == 0 && name == "Android") continue
                        walk(k, depth + 1)
                    }
                }
            }
            walk(root, 0)
            entries = out
            builtAt = System.currentTimeMillis()
        }
    }

    suspend fun ensureFresh(maxAgeMs: Long = 5 * 60_000L) {
        if (System.currentTimeMillis() - builtAt > maxAgeMs) build()
    }

    private val typeGroups = mapOf(
        "photo" to setOf("jpg", "jpeg", "png", "heic", "webp", "gif", "dng"),
        "image" to setOf("jpg", "jpeg", "png", "heic", "webp", "gif", "bmp", "svg", "dng"),
        "picture" to setOf("jpg", "jpeg", "png", "heic", "webp", "gif"),
        "screenshot" to setOf("jpg", "jpeg", "png"),
        "video" to setOf("mp4", "mkv", "mov", "webm", "3gp", "avi"),
        "music" to setOf("mp3", "m4a", "flac", "wav", "ogg", "opus", "aac"),
        "audio" to setOf("mp3", "m4a", "flac", "wav", "ogg", "opus", "aac", "amr"),
        "song" to setOf("mp3", "m4a", "flac", "wav", "ogg", "opus"),
        "pdf" to setOf("pdf"),
        "document" to setOf("pdf", "doc", "docx", "odt", "txt", "rtf", "md", "xls", "xlsx", "ppt", "pptx", "csv"),
        "doc" to setOf("doc", "docx", "pdf", "odt"),
        "spreadsheet" to setOf("xls", "xlsx", "csv", "ods"),
        "excel" to setOf("xls", "xlsx", "csv"),
        "word" to setOf("doc", "docx"),
        "presentation" to setOf("ppt", "pptx", "odp"),
        "text" to setOf("txt", "md", "log", "csv", "json"),
        "note" to setOf("txt", "md"),
        "code" to setOf("py", "js", "ts", "kt", "java", "c", "cpp", "h", "html", "css", "json", "rb", "php", "lua", "go", "rs", "sh", "sql"),
        "script" to setOf("py", "js", "sh", "ts", "rb", "php", "lua"),
        "python" to setOf("py"),
        "javascript" to setOf("js", "mjs"),
        "html" to setOf("html", "htm"),
        "apk" to setOf("apk", "apks", "xapk"),
        "app" to setOf("apk", "apks", "xapk"),
        "zip" to setOf("zip", "rar", "7z", "tar", "gz"),
        "archive" to setOf("zip", "rar", "7z", "tar", "gz"),
        "model" to setOf("gguf"),
        "gguf" to setOf("gguf"),
        "ebook" to setOf("epub", "mobi", "pdf"),
        "book" to setOf("epub", "mobi", "pdf")
    )

    private val stopWords = setOf(
        "the", "a", "an", "my", "me", "i", "you", "your", "can", "could", "please", "find", "search", "look", "for",
        "where", "is", "are", "was", "show", "list", "all", "any", "file", "files", "folder", "folders", "on", "in",
        "of", "phone", "storage", "device", "and", "or", "to", "it", "that", "this", "with", "called", "named", "have",
        "do", "does", "see", "view", "open", "read", "what", "which", "there", "some", "get", "give", "locate",
        "check", "latest", "recent", "newest", "biggest", "largest", "about", "from", "like", "just", "want", "need",
        "into", "inside", "whats", "what's", "how", "many", "much", "know", "tell", "us", "our", "at", "be", "by", "so"
    )

    /** [hints] are type words like "screenshots" that boost files whose path mentions them, without being required. */
    data class Query(val words: List<String>, val exts: Set<String>, val sortRecent: Boolean, val sortBig: Boolean, val hints: List<String> = emptyList())

    fun parseQuery(text: String): Query {
        val lower = text.lowercase()
        val tokens = Regex("[a-z0-9_.\\-]+").findAll(lower).map { it.value.trim('.', '-') }.filter { it.isNotEmpty() }.toList()
        val exts = mutableSetOf<String>()
        val words = mutableListOf<String>()
        val hints = mutableListOf<String>()
        for (t in tokens) {
            val singular = t.removeSuffix("s")
            val group = typeGroups[t] ?: typeGroups[singular]
            when {
                group != null -> { exts += group; hints += singular }
                t.startsWith(".") || (t.length in 2..5 && typeGroups.values.any { t in it } && lower.contains(".$t")) -> exts += t.removePrefix(".")
                t !in stopWords && t.length >= 2 -> words += t
            }
        }
        return Query(
            words.distinct(),
            exts,
            sortRecent = Regex("\\b(latest|recent|newest|last|today|yesterday)\\b").containsMatchIn(lower),
            sortBig = Regex("\\b(biggest|largest|big|huge|space)\\b").containsMatchIn(lower),
            hints = hints.distinct()
        )
    }

    /** Best matches for a natural-language request like "find my CV pdf" or "latest screenshots". */
    fun search(text: String, limit: Int = 25): List<Entry> {
        val q = parseQuery(text)
        if (q.words.isEmpty() && q.exts.isEmpty() && !q.sortRecent && !q.sortBig) return emptyList()
        val all = entries
        val scored = ArrayList<Pair<Entry, Double>>()
        for (e in all) {
            val ext = e.nameLower.substringAfterLast('.', "")
            if (q.exts.isNotEmpty() && (e.isDir || ext !in q.exts)) continue
            var score = 0.0
            if (q.words.isNotEmpty()) {
                val pathLower = e.path.lowercase()
                var hits = 0
                for (w in q.words) {
                    when {
                        e.nameLower == w || e.nameLower.substringBeforeLast('.') == w -> { score += 6; hits++ }
                        e.nameLower.contains(w) -> { score += 3; hits++ }
                        pathLower.contains("/$w") || pathLower.contains(w) -> { score += 1; hits++ }
                    }
                }
                if (hits == 0) continue
                if (hits == q.words.size) score += 4
            } else {
                score = 1.0
            }
            if (q.hints.isNotEmpty()) {
                val pathLower = e.path.lowercase()
                if (q.hints.any { pathLower.contains(it) }) score += 2
            }
            if (e.isDir) score += 0.5
            scored += e to score
        }
        val cmp = when {
            q.sortRecent -> compareByDescending<Pair<Entry, Double>> { it.second }.thenByDescending { it.first.modified }
            q.sortBig -> compareByDescending<Pair<Entry, Double>> { it.second }.thenByDescending { it.first.size }
            else -> compareByDescending<Pair<Entry, Double>> { it.second }.thenByDescending { it.first.modified }
        }
        return scored.sortedWith(cmp).take(limit).map { it.first }
    }

    private val dateFmt = SimpleDateFormat("d MMM yyyy HH:mm", Locale.UK)

    fun format(list: List<Entry>): String = list.joinToString("\n") { e ->
        if (e.isDir) "[folder] ${e.path}"
        else "${e.path}  (${human(e.size)}, ${dateFmt.format(Date(e.modified))})"
    }

    private fun human(b: Long) = when {
        b >= 1L shl 30 -> "%.1f GB".format(b / (1L shl 30).toDouble())
        b >= 1L shl 20 -> "%.1f MB".format(b / (1L shl 20).toDouble())
        b >= 1L shl 10 -> "%.0f KB".format(b / (1L shl 10).toDouble())
        else -> "$b B"
    }

    /** Top-level folders of the phone with how many items each holds — given to the model up front. */
    fun overview(): String {
        val dirs = root.listFiles()?.filter { it.isDirectory && !it.name.startsWith(".") }?.sortedBy { it.name.lowercase() } ?: return ""
        val counts = HashMap<String, Int>()
        val prefix = root.absolutePath + "/"
        for (e in entries) {
            val top = e.path.removePrefix(prefix).substringBefore('/')
            counts[top] = (counts[top] ?: 0) + 1
        }
        return dirs.joinToString(", ") { d -> counts[d.name]?.let { "${d.name} ($it items)" } ?: d.name }
    }
}
