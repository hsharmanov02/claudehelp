package com.localforge.app.data

import android.app.ActivityManager
import android.content.Context
import android.os.Build
import android.os.Environment
import android.os.StatFs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import kotlin.coroutines.coroutineContext

data class LocalModel(val file: File) {
    val name: String get() = file.name
    val size: Long get() = file.length()
    val path: String get() = file.absolutePath
}

data class DeviceInfo(
    val model: String,
    val chip: String,
    val cores: Int,
    val totalRam: Long,
    val availRam: Long,
    val freeStorage: Long
) {
    /** Largest model file this phone can realistically load and still run the OS. */
    val modelBudget: Long get() = (totalRam * 0.5).toLong()
}

/** Only uncensored / abliterated models are allowed in LocalForge. */
object Uncensored {
    private val markers = listOf("uncensored", "abliterat", "ablit", "dolphin", "lexi")
    fun matches(name: String): Boolean = markers.any { name.contains(it, ignoreCase = true) }
}

/** Downloaded GGUF files live in Android/data/com.localforge.app/files/models. */
class ModelStore(private val context: Context) {
    private val prefs = context.getSharedPreferences("models", Context.MODE_PRIVATE)
    val dir: File = (context.getExternalFilesDir("models") ?: File(context.filesDir, "models")).also { it.mkdirs() }

    fun deviceInfo(): DeviceInfo {
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val mi = ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }
        val chip = if (Build.VERSION.SDK_INT >= 31) "${Build.SOC_MANUFACTURER} ${Build.SOC_MODEL}" else Build.HARDWARE
        return DeviceInfo(
            model = "${Build.MANUFACTURER} ${Build.MODEL}",
            chip = chip,
            cores = Runtime.getRuntime().availableProcessors(),
            totalRam = mi.totalMem,
            availRam = mi.availMem,
            freeStorage = StatFs(dir.absolutePath).availableBytes
        )
    }

    /** Models in the app folder plus uncensored GGUFs found elsewhere on the phone by a scan. */
    fun localModels(): List<LocalModel> {
        val own = dir.listFiles { f -> f.isFile && f.name.endsWith(".gguf", true) }?.toList() ?: emptyList()
        val found = (prefs.getStringSet("found", emptySet()) ?: emptySet()).map { File(it) }.filter { it.isFile }
        return (own + found).distinctBy { it.absolutePath }.map { LocalModel(it) }.sortedBy { it.name.lowercase() }
    }

    /** Walks phone storage for uncensored .gguf files (needs all-files access). Returns how many were found. */
    suspend fun scanPhone(): Int = withContext(Dispatchers.IO) {
        val root = Environment.getExternalStorageDirectory()
        val hits = mutableListOf<String>()
        fun walk(d: File, depth: Int) {
            if (depth > 7) return
            val kids = d.listFiles() ?: return
            for (k in kids) {
                if (k.isDirectory) {
                    if (k.name.startsWith(".") || (depth == 0 && k.name == "Android")) continue
                    walk(k, depth + 1)
                } else if (k.name.endsWith(".gguf", true) && k.length() > 50L * 1024 * 1024 &&
                    !k.name.contains("mmproj", true) && Uncensored.matches(k.name)
                ) {
                    hits += k.absolutePath
                }
            }
        }
        walk(root, 0)
        prefs.edit().putStringSet("found", hits.toSet()).apply()
        hits.size
    }

    fun delete(model: LocalModel) {
        if (model.file.parentFile == dir) {
            model.file.delete()
        } else {
            // Found by a scan: just forget it, never delete the user's own copy.
            val set = (prefs.getStringSet("found", emptySet()) ?: emptySet()) - model.path
            prefs.edit().putStringSet("found", set).apply()
        }
    }

    fun partFile(fileName: String) = File(dir, "$fileName.part")

    /**
     * Downloads [file] into the models folder, resuming a previous partial download.
     * [onProgress] gets (bytesDone, bytesTotal). Cancel the coroutine to stop.
     */
    suspend fun download(file: HfFile, token: String?, onProgress: (Long, Long) -> Unit) = withContext(Dispatchers.IO) {
        val part = partFile(file.fileName)
        val target = File(dir, file.fileName)
        var url = URL(HuggingFace.downloadUrl(file))
        var conn: HttpURLConnection
        var redirects = 0
        val start = if (part.exists()) part.length() else 0L
        while (true) {
            conn = url.openConnection() as HttpURLConnection
            conn.instanceFollowRedirects = false
            conn.connectTimeout = 20000
            conn.readTimeout = 60000
            conn.setRequestProperty("User-Agent", "LocalForge/1.0")
            if (start > 0) conn.setRequestProperty("Range", "bytes=$start-")
            // Only send the token to Hugging Face itself, not to its storage servers.
            if (!token.isNullOrBlank() && url.host.endsWith("huggingface.co")) {
                conn.setRequestProperty("Authorization", "Bearer $token")
            }
            val code = conn.responseCode
            if (code in 300..399 && redirects < 8) {
                val loc = conn.getHeaderField("Location") ?: throw IllegalStateException("Bad redirect")
                url = URL(url, loc)
                conn.disconnect()
                redirects++
                continue
            }
            if (code == 401 || code == 403) throw IllegalStateException("This model is gated. Add a Hugging Face token in Home → settings.")
            if (code !in 200..299) throw IllegalStateException("Server returned HTTP $code")
            break
        }
        val resumed = conn.responseCode == 206
        val already = if (resumed) start else 0L
        val total = already + conn.contentLengthLong.coerceAtLeast(0L)
        val expected = if (total > already) total else file.size
        var done = already
        try {
            conn.inputStream.use { input ->
                FileOutputStream(part, resumed).use { out ->
                    val buf = ByteArray(256 * 1024)
                    var lastReport = 0L
                    while (true) {
                        coroutineContext.ensureActive()
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        done += n
                        if (done - lastReport > 2L * 1024 * 1024) {
                            lastReport = done
                            onProgress(done, expected)
                        }
                    }
                }
            }
        } finally {
            conn.disconnect()
        }
        if (expected > 0 && done < expected) throw IllegalStateException("Download was cut off. Tap download again to resume.")
        if (target.exists()) target.delete()
        if (!part.renameTo(target)) throw IllegalStateException("Couldn't save the file")
        onProgress(done, done)
    }
}
