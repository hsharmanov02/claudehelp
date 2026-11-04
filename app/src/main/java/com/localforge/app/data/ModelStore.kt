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
    /** Largest model file that still runs at a usable speed and leaves the phone room to breathe. */
    val modelBudget: Long get() = minOf((totalRam * 0.3).toLong(), 2_600L * 1024 * 1024)
}

/** Only uncensored / abliterated models are allowed in LocalForge. */
object Uncensored {
    private val markers = listOf("uncensored", "abliterat", "ablit", "dolphin", "lexi")
    fun matches(name: String): Boolean = markers.any { name.contains(it, ignoreCase = true) }
}

/**
 * Downloaded GGUF files live in /sdcard/LocalForge/models, shared by LocalForge and LocalForge Autopilot
 * (both need all-files access for it). Without that access, or for models downloaded by older versions,
 * the app's own folder Android/data/<app id>/files/models is used too.
 */
class ModelStore(private val context: Context) {
    private val prefs = context.getSharedPreferences("models", Context.MODE_PRIVATE)
    /** The app's private model folder (older versions downloaded here; other apps can't read it). */
    val privateDir: File = (context.getExternalFilesDir("models") ?: File(context.filesDir, "models")).also { it.mkdirs() }
    private val shared get() = Environment.isExternalStorageManager()
    /** Where new downloads go. */
    val dir: File get() = if (shared) sharedDir().also { it.mkdirs() } else privateDir

    companion object {
        /** One model folder for both apps. */
        fun sharedDir(): File = File(Environment.getExternalStorageDirectory(), "LocalForge/models")
    }

    private fun ggufsIn(d: File): List<File> =
        d.listFiles { f -> f.isFile && f.name.endsWith(".gguf", true) }?.toList() ?: emptyList()

    @Volatile var moving: String? = null
        private set

    /**
     * Moves models from the private folder into the shared one, so the other LocalForge app can use them too.
     * A rename when Android allows it (instant), otherwise a copy that is only switched over once complete.
     * [skip] is the model that's loaded right now: it stays where it is until the next time.
     * Returns how many models were moved.
     */
    suspend fun shareModels(skip: String?): Int = withContext(Dispatchers.IO) {
        if (!shared || privateDir.absolutePath == sharedDir().absolutePath) return@withContext 0
        val target = sharedDir().also { it.mkdirs() }
        var moved = 0
        for (f in ggufsIn(privateDir)) {
            if (f.absolutePath == skip) continue
            val dest = File(target, f.name)
            if (dest.exists() && dest.length() == f.length()) { f.delete(); moved++; continue }
            if (f.renameTo(dest)) { moved++; continue }
            if (StatFs(target.absolutePath).availableBytes < f.length() + 512L * 1024 * 1024) continue
            val part = File(target, f.name + ".moving")
            try {
                moving = f.name
                f.inputStream().use { input -> FileOutputStream(part).use { out -> input.copyTo(out, 1024 * 1024) } }
                if (part.length() == f.length() && part.renameTo(dest)) { f.delete(); moved++ } else part.delete()
            } catch (e: Exception) {
                part.delete()
            } finally {
                moving = null
            }
        }
        moved
    }

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
        val own = ggufsIn(privateDir) + (if (shared) ggufsIn(sharedDir()) else emptyList())
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

    /** True for models in a LocalForge model folder (deleting removes the file); false for ones found by a scan. */
    fun isOwn(model: LocalModel): Boolean {
        val parent = model.file.parentFile?.absolutePath
        return parent == privateDir.absolutePath || parent == sharedDir().absolutePath
    }

    fun delete(model: LocalModel) {
        if (isOwn(model)) {
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
