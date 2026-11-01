package com.localforge.app.data

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import java.io.File

data class LocalModel(val file: File) {
    val name: String get() = file.name
    val size: Long get() = file.length()
}

data class DownloadState(val id: Long, val fileName: String, val done: Long, val total: Long, val status: Int)

/** Downloaded GGUF files live in the app's own external folder (Android/data/com.localforge.app/files/models). */
class ModelStore(private val context: Context) {
    private val prefs = context.getSharedPreferences("downloads", Context.MODE_PRIVATE)
    private val dm = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager

    val dir: File = (context.getExternalFilesDir("models") ?: File(context.filesDir, "models")).also { it.mkdirs() }

    private fun activeIds(): Map<String, Long> =
        prefs.all.mapNotNull { (k, v) -> (v as? Long)?.let { k to it } }.toMap()

    fun localModels(): List<LocalModel> {
        val busy = activeIds().keys
        return dir.listFiles { f -> f.isFile && f.name.endsWith(".gguf", true) && f.name !in busy }
            ?.map { LocalModel(it) }?.sortedBy { it.name } ?: emptyList()
    }

    fun startDownload(file: HfFile, token: String?): Long {
        val target = File(dir, file.fileName)
        if (target.exists()) target.delete()
        val req = DownloadManager.Request(Uri.parse(HuggingFace.downloadUrl(file)))
            .setTitle(file.fileName)
            .setDescription("LocalForge model download")
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalFilesDir(context, "models", file.fileName)
            .setAllowedOverMetered(true)
            .setAllowedOverRoaming(false)
            .addRequestHeader("User-Agent", "LocalForge/1.0")
        if (!token.isNullOrBlank()) req.addRequestHeader("Authorization", "Bearer $token")
        val id = dm.enqueue(req)
        prefs.edit().putLong(file.fileName, id).apply()
        return id
    }

    /** Current state of every tracked download; finished or failed ones are cleaned up. */
    fun pollDownloads(): List<DownloadState> {
        val out = mutableListOf<DownloadState>()
        for ((name, id) in activeIds()) {
            val c = dm.query(DownloadManager.Query().setFilterById(id))
            var state: DownloadState? = null
            c.use {
                if (it.moveToFirst()) {
                    val status = it.getInt(it.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                    val done = it.getLong(it.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR))
                    val total = it.getLong(it.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES))
                    state = DownloadState(id, name, done, total, status)
                }
            }
            val s = state
            when {
                s == null -> prefs.edit().remove(name).apply()
                s.status == DownloadManager.STATUS_SUCCESSFUL -> prefs.edit().remove(name).apply()
                s.status == DownloadManager.STATUS_FAILED -> {
                    prefs.edit().remove(name).apply()
                    File(dir, name).delete()
                    out += s
                }
                else -> out += s
            }
        }
        return out
    }

    fun cancel(fileName: String) {
        activeIds()[fileName]?.let { dm.remove(it) }
        prefs.edit().remove(fileName).apply()
        File(dir, fileName).delete()
    }

    fun delete(model: LocalModel) {
        model.file.delete()
    }
}
