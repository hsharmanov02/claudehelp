package com.localforge.app

import android.app.Application
import android.content.Context
import android.os.Environment
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.localforge.app.agent.FileAction
import com.localforge.app.agent.FileTools
import com.localforge.app.data.DownloadState
import com.localforge.app.data.HfFile
import com.localforge.app.data.HfModel
import com.localforge.app.data.HuggingFace
import com.localforge.app.data.LocalModel
import com.localforge.app.data.ModelStore
import com.localforge.app.engine.ChatMessage
import com.localforge.app.engine.Engine
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

enum class Tab { HOME, MODELS, CODE, FILES }
enum class Role { USER, ASSISTANT, TOOL, NOTE }
enum class ActionStatus { PENDING, DONE, REJECTED }

class PendingAction(val action: FileAction) {
    var status by mutableStateOf(ActionStatus.PENDING)
    var result by mutableStateOf<String?>(null)
}

class UiMessage(val role: Role, initial: String, val modelText: String? = null) {
    var text by mutableStateOf(initial)
    val actions = mutableStateListOf<PendingAction>()
}

class AppViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = app.getSharedPreferences("settings", Context.MODE_PRIVATE)
    val store = ModelStore(app)
    private val main = Handler(Looper.getMainLooper())

    var tab by mutableStateOf(Tab.HOME)
    var hasFileAccess by mutableStateOf(false)
        private set

    // ---- Settings ----
    var nCtx by mutableStateOf(prefs.getInt("nCtx", 4096))
        private set
    var threads by mutableStateOf(prefs.getInt("threads", defaultThreads()))
        private set
    var temperature by mutableStateOf(prefs.getFloat("temp", 0.3f))
        private set
    var maxTokens by mutableStateOf(prefs.getInt("maxTokens", 1024))
        private set
    var autoApprove by mutableStateOf(prefs.getBoolean("autoApprove", false))
        private set
    var hfToken by mutableStateOf(prefs.getString("hfToken", "") ?: "")
        private set

    fun updateCtx(v: Int) { nCtx = v; prefs.edit().putInt("nCtx", v).apply() }
    fun updateThreads(v: Int) { threads = v; prefs.edit().putInt("threads", v).apply() }
    fun updateTemperature(v: Float) { temperature = v; prefs.edit().putFloat("temp", v).apply() }
    fun updateMaxTokens(v: Int) { maxTokens = v; prefs.edit().putInt("maxTokens", v).apply() }
    fun updateAutoApprove(v: Boolean) { autoApprove = v; prefs.edit().putBoolean("autoApprove", v).apply() }
    fun updateHfToken(v: String) { hfToken = v.trim(); prefs.edit().putString("hfToken", hfToken).apply() }

    private fun defaultThreads(): Int = (Runtime.getRuntime().availableProcessors() - 2).coerceIn(2, 6)

    // ---- Models ----
    val localModels = mutableStateListOf<LocalModel>()
    val downloads = mutableStateListOf<DownloadState>()
    var loadedModel by mutableStateOf<String?>(null)
        private set
    var loading by mutableStateOf(false)
        private set
    var engineMessage by mutableStateOf<String?>(null)

    var searchQuery by mutableStateOf("")
    val searchResults = mutableStateListOf<HfModel>()
    var searching by mutableStateOf(false)
        private set
    var searchError by mutableStateOf<String?>(null)
        private set
    var openRepo by mutableStateOf<String?>(null)
        private set
    val repoFiles = mutableStateListOf<HfFile>()
    var repoLoading by mutableStateOf(false)
        private set
    private var pollJob: Job? = null

    fun refreshPermission() {
        hasFileAccess = Environment.isExternalStorageManager()
        if (hasFileAccess && entries.isEmpty()) refreshFiles()
    }

    fun refreshModels() {
        localModels.clear()
        localModels.addAll(store.localModels())
    }

    fun search(query: String = searchQuery) {
        searchQuery = query
        if (query.isBlank()) return
        viewModelScope.launch {
            searching = true
            searchError = null
            openRepo = null
            try {
                val r = HuggingFace.search(query, hfToken)
                searchResults.clear()
                searchResults.addAll(r)
                if (r.isEmpty()) searchError = "No GGUF models found for \"$query\""
            } catch (e: Exception) {
                searchError = "Search failed: ${e.message}"
            } finally {
                searching = false
            }
        }
    }

    fun selectRepo(id: String?) {
        openRepo = id
        repoFiles.clear()
        if (id == null) return
        viewModelScope.launch {
            repoLoading = true
            try {
                repoFiles.addAll(HuggingFace.ggufFiles(id, hfToken))
            } catch (e: Exception) {
                searchError = "Couldn't list files: ${e.message}"
            } finally {
                repoLoading = false
            }
        }
    }

    fun download(file: HfFile) {
        try {
            store.startDownload(file, hfToken)
            engineMessage = "Downloading ${file.fileName}"
            startPolling()
        } catch (e: Exception) {
            engineMessage = "Download failed to start: ${e.message}"
        }
    }

    fun cancelDownload(name: String) {
        store.cancel(name)
        downloads.removeAll { it.fileName == name }
    }

    private fun startPolling() {
        if (pollJob?.isActive == true) return
        pollJob = viewModelScope.launch {
            while (true) {
                val states = withContext(Dispatchers.IO) { store.pollDownloads() }
                val failed = states.filter { it.status == android.app.DownloadManager.STATUS_FAILED }
                failed.forEach { engineMessage = "Download failed: ${it.fileName}" }
                val active = states - failed.toSet()
                val finished = downloads.size > active.size
                downloads.clear()
                downloads.addAll(active)
                if (finished) refreshModels()
                if (active.isEmpty()) break
                delay(1000)
            }
            refreshModels()
        }
    }

    fun deleteModel(m: LocalModel) {
        viewModelScope.launch {
            if (loadedModel == m.name) unloadModel()
            store.delete(m)
            refreshModels()
        }
    }

    fun loadModel(m: LocalModel) {
        if (loading) return
        viewModelScope.launch {
            loading = true
            engineMessage = "Loading ${m.name}…"
            val ok = try {
                Engine.load(m.file.absolutePath, nCtx, threads)
            } catch (e: Throwable) {
                false
            }
            loading = false
            if (ok) {
                loadedModel = m.name
                engineMessage = "${m.name} is ready"
                prefs.edit().putString("lastModel", m.name).apply()
            } else {
                loadedModel = null
                engineMessage = "Couldn't load ${m.name}. It may be too big for this phone's memory or not a valid GGUF."
            }
        }
    }

    fun unloadModel() {
        viewModelScope.launch {
            Engine.unload()
            loadedModel = null
            engineMessage = "Model unloaded"
        }
    }

    val lastModelName: String? get() = prefs.getString("lastModel", null)

    // ---- Code assistant ----
    val messages = mutableStateListOf<UiMessage>()
    var input by mutableStateOf("")
    var attachment by mutableStateOf<File?>(null)
    var generating by mutableStateOf(false)
        private set
    private var genJob: Job? = null

    val workspaceRoot: String get() = Environment.getExternalStorageDirectory().absolutePath

    fun attachFile(file: File) {
        attachment = file
        if (input.isBlank()) input = "Explain this file and suggest improvements."
        tab = Tab.CODE
    }

    fun clearChat() {
        if (generating) return
        messages.clear()
    }

    @Volatile private var stopRequested = false

    fun stopGenerating() {
        stopRequested = true
        Engine.stop()
    }

    fun send() {
        val text = input.trim()
        if (text.isEmpty() || generating) return
        if (!Engine.isLoaded) {
            messages += UiMessage(Role.NOTE, "Load a model on the Home tab first.")
            return
        }
        val att = attachment
        var modelText: String? = null
        var display = text
        if (att != null) {
            val body = try {
                att.bufferedReader().use { r ->
                    val buf = CharArray(12_000)
                    val n = r.read(buf)
                    if (n > 0) String(buf, 0, n) else ""
                }
            } catch (e: Exception) {
                "(couldn't read file: ${e.message})"
            }
            modelText = "$text\n\nFile ${att.absolutePath}:\n```\n$body\n```"
            display = "📎 ${att.name}\n$text"
        }
        input = ""
        attachment = null
        messages += UiMessage(Role.USER, display, modelText)
        runAgent()
    }

    private fun history(): List<ChatMessage> {
        val budget = ((nCtx - maxTokens - 400).coerceAtLeast(512)) * 3
        val picked = ArrayDeque<ChatMessage>()
        var used = 0
        for (m in messages.asReversed()) {
            val msg = when (m.role) {
                Role.USER -> ChatMessage("user", m.modelText ?: m.text)
                Role.ASSISTANT -> if (m.text.isBlank()) null else ChatMessage("assistant", m.text)
                Role.TOOL -> ChatMessage("user", "[File results]\n${m.text}")
                Role.NOTE -> null
            } ?: continue
            if (used + msg.content.length > budget && picked.isNotEmpty()) break
            used += msg.content.length
            picked.addFirst(msg)
        }
        // Templates expect the conversation to start with a user turn.
        while (picked.isNotEmpty() && picked.first().role != "user") picked.removeFirst()
        return listOf(ChatMessage("system", FileTools.systemPrompt(workspaceRoot))) + picked
    }

    private fun runAgent() {
        genJob = viewModelScope.launch {
            generating = true
            stopRequested = false
            try {
                for (step in 0 until 5) {
                    val reply = UiMessage(Role.ASSISTANT, "")
                    messages += reply
                    val full = Engine.chat(history(), maxTokens, temperature) { piece ->
                        main.post { reply.text += piece }
                    }
                    reply.text = full.trim()
                    if (reply.text.isEmpty()) reply.text = "(no reply)"
                    if (stopRequested) {
                        messages += UiMessage(Role.NOTE, "Stopped.")
                        break
                    }

                    val actions = FileTools.parse(full)
                    if (actions.isEmpty()) break
                    for (a in actions.filter { !it.readOnly }) {
                        val p = PendingAction(a)
                        reply.actions += p
                        if (autoApprove && a !is FileAction.Delete && hasFileAccess) approve(p)
                    }
                    val reads = actions.filter { it.readOnly }
                    if (reads.isEmpty()) break
                    if (!hasFileAccess) {
                        messages += UiMessage(Role.NOTE, "The assistant wants to look at your files. Grant all-files access on the Files tab.")
                        break
                    }
                    val results = withContext(Dispatchers.IO) { reads.map { FileTools.execute(it) } }
                    messages += UiMessage(Role.TOOL, results.joinToString("\n\n"))
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                messages += UiMessage(Role.NOTE, e.message ?: "Something went wrong")
            } finally {
                generating = false
            }
        }
    }

    fun approve(p: PendingAction) {
        if (p.status != ActionStatus.PENDING) return
        if (!hasFileAccess) {
            p.result = "Grant all-files access on the Files tab first."
            return
        }
        p.status = ActionStatus.DONE
        viewModelScope.launch {
            p.result = withContext(Dispatchers.IO) { FileTools.execute(p.action) }
            refreshFiles()
        }
    }

    fun reject(p: PendingAction) {
        if (p.status == ActionStatus.PENDING) p.status = ActionStatus.REJECTED
    }

    // ---- File manager ----
    var currentDir by mutableStateOf(Environment.getExternalStorageDirectory())
        private set
    val entries = mutableStateListOf<File>()
    var fileMessage by mutableStateOf<String?>(null)
    var editingFile by mutableStateOf<File?>(null)
        private set
    var editorText by mutableStateOf("")
    var editorReadOnly by mutableStateOf(false)
        private set

    fun refreshFiles() {
        val list = currentDir.listFiles()?.sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))
        entries.clear()
        if (list != null) entries.addAll(list)
    }

    fun openDir(dir: File) {
        currentDir = dir
        refreshFiles()
    }

    fun goUp(): Boolean {
        val root = Environment.getExternalStorageDirectory()
        val parent = currentDir.parentFile
        if (currentDir == root || parent == null) return false
        openDir(parent)
        return true
    }

    private fun fileOp(block: () -> String) {
        fileMessage = try { block() } catch (e: Exception) { "Error: ${e.message}" }
        refreshFiles()
    }

    fun createFile(name: String) = fileOp {
        val f = File(currentDir, name.trim())
        if (f.exists()) "${f.name} already exists" else if (f.createNewFile()) "Created ${f.name}" else "Couldn't create ${f.name}"
    }

    fun createFolder(name: String) = fileOp {
        val f = File(currentDir, name.trim())
        if (f.mkdirs()) "Created folder ${f.name}" else "Couldn't create ${f.name}"
    }

    fun rename(file: File, newName: String) = fileOp {
        val target = File(file.parentFile, newName.trim())
        if (target.exists()) "${target.name} already exists"
        else if (file.renameTo(target)) "Renamed to ${target.name}" else "Couldn't rename ${file.name}"
    }

    fun delete(file: File) = fileOp {
        if (file.deleteRecursively()) "Deleted ${file.name}" else "Couldn't delete ${file.name}"
    }

    fun openEditor(file: File) {
        editorReadOnly = false
        editorText = when {
            file.length() > 512 * 1024 -> { editorReadOnly = true; "File is too large to edit here (${file.length() / 1024} KB)." }
            else -> try {
                val bytes = file.readBytes()
                if (bytes.take(4000).any { it == 0.toByte() }) {
                    editorReadOnly = true
                    "This looks like a binary file and can't be shown as text."
                } else String(bytes, Charsets.UTF_8)
            } catch (e: Exception) {
                editorReadOnly = true
                "Couldn't open: ${e.message}"
            }
        }
        editingFile = file
    }

    fun saveEditor() {
        val f = editingFile ?: return
        if (editorReadOnly) return
        fileOp { f.writeText(editorText); "Saved ${f.name}" }
    }

    fun closeEditor() {
        editingFile = null
        editorText = ""
    }

    init {
        refreshPermission()
        refreshModels()
        startPolling()
    }
}
