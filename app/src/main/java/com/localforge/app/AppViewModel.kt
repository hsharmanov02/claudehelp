package com.localforge.app

import android.app.Application
import android.content.Context
import android.os.Environment
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import org.json.JSONArray
import org.json.JSONObject
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.localforge.app.agent.CodeRunner
import com.localforge.app.agent.FileIndex
import com.localforge.app.agent.TermuxBridge
import com.localforge.app.agent.BrowserTools
import com.localforge.app.agent.WebBrowser
import com.localforge.app.agent.FileAction
import com.localforge.app.agent.FileTools
import com.localforge.app.data.DeviceInfo
import com.localforge.app.data.Recommendation
import com.localforge.app.data.formatBytes
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

enum class Tab { HOME, MODELS, CODE, BROWSER, FILES }
enum class Role { USER, ASSISTANT, TOOL, RUN, NOTE }
enum class ActionStatus { PENDING, DONE, REJECTED }

class PendingAction(val action: FileAction) {
    var status by mutableStateOf(ActionStatus.PENDING)
    var result by mutableStateOf<String?>(null)
    /** Steps the model wrote after this card; they run once it's approved. */
    var queued: List<FileAction> = emptyList()
}

class DownloadProgress(val file: HfFile) {
    var done by mutableStateOf(0L)
    var total by mutableStateOf(file.size)
    var error by mutableStateOf<String?>(null)
    var job: Job? = null
}

class UiMessage(val role: Role, initial: String, private val baseModelText: String? = null) {
    /** Text the model sees instead of [text], if different. */
    var modelTextOverride: String? = null
    val modelText: String? get() = modelTextOverride ?: baseModelText
    var text by mutableStateOf(initial)
    var stats by mutableStateOf<String?>(null)
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
    var threads by mutableStateOf(prefs.getInt("threads2", defaultThreads()))
        private set
    var temperature by mutableStateOf(prefs.getFloat("temp", 0.3f))
        private set
    var maxTokens by mutableStateOf(prefs.getInt("maxTokens", 1024))
        private set
    var autoApprove by mutableStateOf(prefs.getBoolean("autoApprove", false))
        private set
    var hfToken by mutableStateOf(prefs.getString("hfToken", "") ?: "")
        private set
    var browserAutoApprove by mutableStateOf(prefs.getBoolean("browserAutoApprove", false))
        private set
    /** "Ask AI about this page": the chat as a sheet over the live page in the Browser tab. */
    var pagePanelOpen by mutableStateOf(false)

    fun updateCtx(v: Int) { nCtx = v; prefs.edit().putInt("nCtx", v).apply() }
    fun updateThreads(v: Int) { threads = v; prefs.edit().putInt("threads2", v).apply() }
    fun updateTemperature(v: Float) { temperature = v; prefs.edit().putFloat("temp", v).apply() }
    fun updateMaxTokens(v: Int) { maxTokens = v; prefs.edit().putInt("maxTokens", v).apply() }
    fun updateAutoApprove(v: Boolean) { autoApprove = v; prefs.edit().putBoolean("autoApprove", v).apply() }
    fun updateBrowserAutoApprove(v: Boolean) { browserAutoApprove = v; prefs.edit().putBoolean("browserAutoApprove", v).apply() }
    fun updateHfToken(v: String) { hfToken = v.trim(); prefs.edit().putString("hfToken", hfToken).apply() }

    // Phones have 4 fast cores at most; using the slow ones too makes generation slower, not faster.
    private fun defaultThreads(): Int = Runtime.getRuntime().availableProcessors().coerceAtMost(4).coerceAtLeast(2)

    // ---- Models ----
    val localModels = mutableStateListOf<LocalModel>()
    /** Active or failed downloads, keyed by file name. */
    val downloads = mutableStateMapOf<String, DownloadProgress>()
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

    // ---- Phone scan + recommendations ----
    var device by mutableStateOf<DeviceInfo?>(null)
        private set
    val recommendations = mutableStateListOf<Recommendation>()
    var scanning by mutableStateOf(false)
        private set
    var scanSummary by mutableStateOf<String?>(null)
        private set

    fun refreshPermission() {
        hasFileAccess = Environment.isExternalStorageManager()
        if (hasFileAccess && entries.isEmpty()) refreshFiles()
        if (hasFileAccess) viewModelScope.launch { try { FileIndex.ensureFresh() } catch (_: Exception) {} }
    }

    fun refreshModels() {
        localModels.clear()
        localModels.addAll(store.localModels())
    }

    fun scanPhone() {
        if (scanning) return
        viewModelScope.launch {
            scanning = true
            scanSummary = "Scanning your phone…"
            try {
                val d = withContext(Dispatchers.IO) { store.deviceInfo() }
                device = d
                val found = if (hasFileAccess) store.scanPhone() else -1
                refreshModels()
                scanSummary = (if (found >= 0) "Found $found uncensored model file(s) already on your phone. " else "Allow all-files access to also find models already on your phone. ") +
                    "Finding the best uncensored models for ${formatBytes(d.totalRam)} RAM…"
                val recs = HuggingFace.recommend(d, hfToken, coding = true)
                recommendations.clear()
                recommendations.addAll(recs)
                scanSummary = (if (found >= 0) "Found $found uncensored model file(s) on your phone. " else "") +
                    if (recs.isEmpty()) "Couldn't reach Hugging Face or nothing fits — check your connection." else "Top picks for your phone are below."
            } catch (e: Exception) {
                scanSummary = "Scan failed: ${e.message}"
            } finally {
                scanning = false
            }
        }
    }

    fun search(query: String = searchQuery) {
        searchQuery = query
        viewModelScope.launch {
            searching = true
            searchError = null
            openRepo = null
            try {
                val r = HuggingFace.search(query, hfToken)
                searchResults.clear()
                searchResults.addAll(r)
                if (r.isEmpty()) searchError = "No uncensored GGUF models found for \"$query\""
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
                if (repoFiles.isEmpty()) searchError = "No single-file GGUF models in $id"
            } catch (e: Exception) {
                searchError = "Couldn't list files: ${e.message}"
            } finally {
                repoLoading = false
            }
        }
    }

    fun download(file: HfFile) {
        val existing = downloads[file.fileName]
        if (existing != null && existing.job?.isActive == true) return
        val free = store.deviceInfo().freeStorage
        if (file.size > 0 && file.size > free - 200L * 1024 * 1024) {
            engineMessage = "Not enough free storage for ${file.fileName} (${formatBytes(file.size)} needed, ${formatBytes(free)} free)."
            return
        }
        val p = DownloadProgress(file)
        downloads[file.fileName] = p
        engineMessage = "Downloading ${file.fileName}"
        p.job = viewModelScope.launch {
            try {
                store.download(file, hfToken) { done, total ->
                    main.post { p.done = done; p.total = total }
                }
                downloads.remove(file.fileName)
                refreshModels()
                engineMessage = "Downloaded ${file.fileName}. Load it on the Home tab."
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                p.error = e.message ?: "Download failed"
            }
        }
    }

    fun cancelDownload(name: String) {
        downloads[name]?.job?.cancel()
        downloads.remove(name)
        store.partFile(name).delete()
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
        viewModelScope.launch { loadModelNow(m) }
    }

    /** Loads a model and waits until it's ready. Returns true on success. */
    private suspend fun loadModelNow(m: LocalModel): Boolean {
        loading = true
        engineMessage = "Loading ${m.name}…"
        var err: String? = null
        val ok = try {
            Engine.load(m.file.absolutePath, nCtx, threads, getApplication<Application>().applicationInfo.nativeLibraryDir)
        } catch (e: CancellationException) {
            loading = false
            throw e
        } catch (e: Throwable) {
            err = e.message
            false
        }
        loading = false
        if (ok) {
            loadedModel = m.name
            engineMessage = "${m.name} is ready"
            prefs.edit().putString("lastModel", m.name).apply()
        } else {
            loadedModel = null
            engineMessage = err ?: "Couldn't load ${m.name}. It may be too big for this phone's memory or not a valid GGUF."
        }
        return ok
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
        try { chatFile.delete() } catch (_: Exception) {}
    }

    @Volatile private var stopRequested = false

    fun stopGenerating() {
        stopRequested = true
        Engine.stop()
    }

    /** [onPage]: asked from the panel over the browser, so the question is about the page that's open. */
    fun send(onPage: Boolean = false) {
        val text = input.trim()
        if (text.isEmpty() || generating || loading) return
        if (!Engine.isLoaded) {
            // The model is unloaded if Android restarted the app; reload the last one automatically.
            val last = localModels.firstOrNull { it.name == lastModelName } ?: localModels.singleOrNull()
            if (last == null) {
                messages += UiMessage(Role.NOTE, "Load a model on the Home tab first.")
                return
            }
            viewModelScope.launch {
                messages += UiMessage(Role.NOTE, "Loading ${last.name.removeSuffix(".gguf")}…")
                if (loadModelNow(last)) {
                    send(onPage)
                } else {
                    messages.add(UiMessage(Role.NOTE, engineMessage ?: "Couldn't load the model."))
                }
            }
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
        if (onPage && WebBrowser.hasPage) {
            modelText = "[I'm looking at this page in your browser: \"${WebBrowser.title}\" ${WebBrowser.url}. " +
                "My request is about it. If I ask you to open/click something, read the page with <page/> and click it first; " +
                "for data, use <extract/>. Then click/type on it as needed.]\n" + (modelText ?: text)
        }
        input = ""
        attachment = null
        val userMsg = UiMessage(Role.USER, display, modelText)
        messages += userMsg
        stepsLeft = 10
        totalSteps = 0
        resetRequestState()
        if (onPage || webWords.containsMatchIn(text) || !looksLikeFileRequest(text)) {
            runAgent()
            return
        }
        if (!hasFileAccess) {
            messages += UiMessage(Role.NOTE, "To let the AI see your files, allow all-files access: Home → checklist → Allow all-files access.")
            runAgent()
            return
        }
        // Small models rarely explore files on their own, so the app looks first and hands over the results.
        viewModelScope.launch {
            generating = true
            val ctx = try {
                withContext(Dispatchers.IO) { gatherFileContext(text) }
            } catch (e: Exception) {
                null
            } finally {
                generating = false
            }
            if (!ctx.isNullOrBlank()) {
                userMsg.modelTextOverride = (modelText ?: text) + "\n\n[Phone files — found automatically by the app]\n" + ctx
                messages += UiMessage(Role.TOOL, ctx)
            }
            runAgent()
        }
    }

    private val fileWords = Regex(
        "\\b(file|files|folder|folders|director(y|ies)|storage|download|downloads|document|documents|photo|photos|picture|pictures|" +
            "image|images|screenshot|screenshots|video|videos|music|song|songs|audio|pdf|pdfs|apk|apks|zip|dcim|camera|whatsapp|" +
            "find|search|locate|where|list|open|read|show me|look in|in my|on my phone|sdcard|internal)\\b",
        RegexOption.IGNORE_CASE
    )

    private fun looksLikeFileRequest(text: String): Boolean =
        fileWords.containsMatchIn(text) || text.contains("/storage/") || Regex("\\.[a-z0-9]{2,5}\\b").containsMatchIn(text.lowercase())

    /** Finds what the user is talking about: explicit paths, named folders, and index matches. */
    private suspend fun gatherFileContext(text: String): String {
        FileIndex.ensureFresh()
        val root = Environment.getExternalStorageDirectory()
        val parts = mutableListOf<String>()
        val used = mutableSetOf<String>()

        // 1) Full paths in the message: list folders, read files.
        for (m in Regex("/storage/[^\\s\"'`]+").findAll(text)) {
            val f = File(m.value.trimEnd('.', ',', ')', '?', '!'))
            if (!used.add(f.absolutePath)) continue
            when {
                f.isDirectory -> parts += FileTools.execute(FileAction.ListDir(f.absolutePath))
                f.isFile -> parts += FileTools.execute(FileAction.Read(f.absolutePath))
            }
        }

        // 2) Top-level folders mentioned by name ("my Download folder", "DCIM", "documents").
        val lower = text.lowercase()
        root.listFiles()?.filter { it.isDirectory && !it.name.startsWith(".") }?.forEach { d ->
            val n = d.name.lowercase()
            val mentioned = Regex("\\b${Regex.escape(n)}s?\\b").containsMatchIn(lower) ||
                (n == "download" && lower.contains("downloads")) || (n == "dcim" && lower.contains("camera"))
            if (mentioned && used.add(d.absolutePath)) parts += FileTools.execute(FileAction.ListDir(d.absolutePath))
        }

        // 3) Whole-phone search by name / type.
        val hits = FileIndex.search(text, 25).filter { it.path !in used }
        if (hits.isNotEmpty()) parts += "Best matches on the phone:\n" + FileIndex.format(hits)

        // 4) Nothing specific: give an overview so the model at least knows what's there.
        if (parts.isEmpty()) {
            parts += "Phone storage ($root) top-level folders: " + FileIndex.overview()
        }
        return parts.joinToString("\n\n").take(9000)
    }

    /** Folder where the assistant's code runs: /LocalForge on phone storage, or the app's own folder. */
    private fun runDir(): File =
        if (hasFileAccess) File(Environment.getExternalStorageDirectory(), "LocalForge")
        else (getApplication<Application>().getExternalFilesDir("run") ?: File(getApplication<Application>().filesDir, "run"))

    // ---- Code runners ----
    var termuxInstalled by mutableStateOf(false)
        private set
    var termuxAllowed by mutableStateOf(false)
        private set
    var runnerMessage by mutableStateOf<String?>(null)
    /** HTML page the assistant (or the user) ran, shown full-screen. */
    var htmlPreview by mutableStateOf<File?>(null)

    fun refreshTermux() {
        val ctx = getApplication<Application>()
        termuxInstalled = TermuxBridge.isInstalled(ctx)
        termuxAllowed = TermuxBridge.hasPermission(ctx)
    }

    fun availableLanguages(): List<String> {
        val termux = termuxInstalled && termuxAllowed && hasFileAccess
        return if (termux) {
            listOf("python", "javascript", "typescript", "html", "sql", "shell", "c", "cpp", "java", "kotlin", "go", "rust", "ruby", "php", "lua", "perl", "bash")
        } else {
            listOf(
                "python", "javascript", "typescript", "html", "sql", "shell", "lua", "ruby",
                "php (basic: no printf)", "c / cpp (simple interpreter: use arrays, printf and cout only — no vector, map or std::string)"
            )
        }
    }

    /** Quick check that Termux answers and which compilers are installed. */
    fun testTermux() {
        viewModelScope.launch {
            runnerMessage = "Testing Termux…"
            val r = CodeRunner.run(
                getApplication<Application>(), "bash",
                "for t in clang go rustc java kotlinc ruby php lua5.4 perl node; do if command -v \$t >/dev/null; then echo \"✓ \$t\"; else echo \"✗ \$t (missing)\"; fi; done",
                runDir()
            )
            runnerMessage = if (r.ok) "Termux works!\n${r.output}" else r.output
        }
    }

    /** Model turns left for the current request (each run/read result triggers another turn). */
    private var stepsLeft = 0

    private fun history(): List<ChatMessage> {
        val overview = if (hasFileAccess) FileIndex.overview().ifBlank { "(not indexed yet)" } else "(no access yet — the user must allow all-files access on the Home tab)"
        val sys = FileTools.systemPrompt(workspaceRoot, runDir().absolutePath, availableLanguages(), overview)
        // Room for the reply, then the system prompt; the rest (in characters, ~3 per token) is for the chat.
        val replyRoom = minOf(maxTokens, maxOf(384, nCtx / 3))
        val budget = maxOf(600, ((nCtx - replyRoom - 200) * 3) - sys.length)
        val picked = ArrayDeque<ChatMessage>()
        var used = 0
        var newestResult = true
        for (m in messages.asReversed()) {
            var msg = when (m.role) {
                Role.USER -> ChatMessage("user", m.modelText ?: m.text)
                Role.ASSISTANT -> if (m.text.isBlank()) null else ChatMessage("assistant", m.text)
                Role.TOOL, Role.RUN -> {
                    var body = if (m.role == Role.TOOL) m.text else (m.modelText ?: m.text)
                    if (!newestResult && body.length > 1200) body = body.take(1200) + "\n…[older result cut]"
                    newestResult = false
                    ChatMessage("user", (if (m.role == Role.TOOL) "[Tool results]\n" else "[Code output]\n") + body)
                }
                Role.NOTE -> null
            } ?: continue
            if (used + msg.content.length > budget) {
                if (picked.isNotEmpty()) break
                msg = ChatMessage(msg.role, msg.content.take(budget) + "\n…[cut to fit the model's memory]")
            }
            used += msg.content.length
            picked.addFirst(msg)
        }
        // Many chat templates need alternating turns: merge neighbours with the same role.
        val merged = mutableListOf<ChatMessage>()
        for (m in picked) {
            val last = merged.lastOrNull()
            if (last != null && last.role == m.role) merged[merged.size - 1] = ChatMessage(m.role, last.content + "\n\n" + m.content)
            else merged += m
        }
        while (merged.isNotEmpty() && merged.first().role != "user") merged.removeAt(0)
        if (merged.isEmpty()) merged += ChatMessage("user", lastUserText().take(budget).ifBlank { "Hello" })
        else if (merged.last().role == "user" && picked.lastOrNull()?.content?.let { it.startsWith("[Tool results]") || it.startsWith("[Code output]") } == true) {
            // After tool output small models forget what was asked: repeat the question.
            val q = messages.lastOrNull { it.role == Role.USER }?.text?.take(500)
            if (!q.isNullOrBlank()) merged[merged.size - 1] = ChatMessage("user", merged.last().content +
                "\n\n[The user's question: $q]\nIf the results above answer it, answer it now, fully and directly.")
        }
        return listOf(ChatMessage("system", sys)) + merged
    }

    private fun lastUserText(): String =
        messages.lastOrNull { it.role == Role.USER }?.let { it.modelText ?: it.text } ?: ""

    /** Runs code and adds its output to the chat. Returns true if it ran. */
    private suspend fun executeRun(run: FileAction.Run, p: PendingAction?): Boolean {
        val out = UiMessage(Role.RUN, "Running ${run.path}…")
        messages += out
        runningCode = true
        val r = try {
            CodeRunner.run(getApplication<Application>(), run.path, run.code, runDir())
        } finally {
            runningCode = false
        }
        out.text = (if (r.ok) "✓ " else if (r.timedOut) "⏱ " else "✗ ") + "${r.lang}\n${r.output}"
        out.modelTextOverride = r.forModel()
        r.previewFile?.let { f ->
            // HTML opens in LocalForge's browser, so the AI sees the page, its buttons and any errors, and can test it.
            showBrowser()
            val report = browserStep(FileAction.Browser("browse", f.absolutePath)) { WebBrowser.openPage(f) }
            val ok = !report.contains("[error]") && !report.contains("Uncaught")
            out.text = (if (ok) "✓ " else "✗ ") + "html (browser)\nSaved ${f.absolutePath} and opened it in the Browser tab."
            out.modelTextOverride = "Your HTML page was saved to ${f.absolutePath} and opened in your browser. " +
                "Test it with <click>/<type>/<js> and fix any errors.\n\n" + report
            ranCode = true
        }
        if (!r.ok) failedCode += codeKey(run.path, run.code)
        ranCode = true
        p?.result = if (r.ok) "Ran successfully" else if (r.timedOut) "Stopped: took too long" else "Failed — see output"
        refreshFiles()
        return true
    }

    // ---- Per-request agent state (mirrors the desktop app) ----
    private var totalSteps = 0
    private var repeats = 0
    private var lastReply: String? = null
    private var ranCode = false
    private var moved = false
    private var moveNudged = false
    private val seenUrls = mutableSetOf<String>()
    private val doneLookups = mutableSetOf<String>()
    private val blockedSites = mutableSetOf<String>()
    private val failedCode = mutableSetOf<String>()

    private fun resetRequestState() {
        repeats = 0; lastReply = null; ranCode = false; moved = false; moveNudged = false
        seenUrls.clear(); doneLookups.clear(); blockedSites.clear(); failedCode.clear()
    }

    private val webWords = Regex(
        "\\b(web|website|site|online|internet|google|search the|browse|browser|news|latest|price|prices|deal|deals|weather|" +
            "amazon|ebay|youtube|reddit|wikipedia|hacker news|www)\\b|https?://|\\b[\\w-]+\\.(com|co\\.uk|org|net|io)\\b",
        RegexOption.IGNORE_CASE
    )
    private val dataWords = Regex(
        "\\b(extract|scrape|table|list|top \\d+|prices?|cheapest|compare|rank(ed|ing)?|csv|deals?|stories|results|data|" +
            "population|market cap|sorted?|how many)\\b", RegexOption.IGNORE_CASE
    )
    private val navAsk = Regex("\\b(open|click|go to|go into|navigate|visit|switch to|press)\\b", RegexOption.IGNORE_CASE)
    private val tagAny = Regex("<(write|run|type|js)\\b[\\s\\S]*?(</\\1>|$)|<[a-z]+\\b[^>]*/?>", RegexOption.IGNORE_CASE)
    private val claims = Regex(
        "ran successfully|run successfully|executed successfully|(saved|written|created) (it |the file |the csv |successfully)|tests? (all )?pass",
        RegexOption.IGNORE_CASE
    )

    /** The reply with tool tags removed: what the user actually reads. */
    private fun proseOf(reply: String): String = FileTools.stripThinking(reply).replace(tagAny, "").trim()

    private fun codeKey(lang: String, code: String): String =
        lang + "|" + code.replace(Regex("/\\*[\\s\\S]*?\\*/"), "").replace(Regex("(?m)(--|#|//)[^\\n'\"]*$"), "").replace(Regex("\\s+"), " ").trim()

    /** Shows the Browser tab with the chat panel open, so the user can watch the AI work on the page. */
    private fun showBrowser() {
        main.post {
            if (tab != Tab.BROWSER) { tab = Tab.BROWSER; pagePanelOpen = true }
        }
    }

    /** Runs one browser step with its label shown live over the page. */
    private suspend fun browserStep(a: FileAction.Browser, run: suspend () -> String = { BrowserTools.execute(a) }): String {
        if (a.kind in setOf("browse", "click", "type", "press")) moved = true
        WebBrowser.step = BrowserTools.chip(a.kind, a.path, a.extra)
        return try { run() } finally { WebBrowser.step = null }
    }

    private fun siteFor(a: FileAction.Browser): String {
        val u = when (a.kind) {
            "browse", "fetch" -> a.path
            "page", "click", "type", "press", "js", "scroll" -> WebBrowser.url
            else -> ""
        }
        return if (Regex("^(https?://)?[\\w-]+(\\.[\\w-]+)+(/|$|:)", RegexOption.IGNORE_CASE).containsMatchIn(u.trim())) BrowserTools.siteOf(u) else ""
    }

    /** Small models often cut a link short (…/japan/toky). If it's the start of exactly one link we've seen, use that. */
    private fun fixUrl(a: FileAction.Browser): FileAction.Browser {
        val u = a.path.trim()
        if (u.isEmpty() || u in seenUrls) return a
        val path = u.replace(Regex("^https?://[^/]+"), "")
        if (u.endsWith("/") || !Regex("[A-Za-z0-9]$").containsMatchIn(u) || path.count { it == '/' } < 1 || path.length < 3) return a
        val matches = seenUrls.filter { it.startsWith(u) && it.length - u.length <= 12 && Regex("[A-Za-z0-9_-]+/?").matches(it.substring(u.length)) }
        return if (matches.size == 1) a.copy(path = matches[0]) else a
    }

    /** After clicks or typing the page is different, so reading or reopening it is allowed again. */
    private fun pageChanged() {
        doneLookups.removeAll { k -> listOf("browse|", "page|", "fetch|", "extract|").any { k.startsWith(it) } }
    }

    private fun noteResult(a: FileAction.Browser, r: String) {
        if (a.kind == "js" && (r.startsWith("Browser error") || r.take(80).contains("returned nothing") ||
                Regex("^Script result:\\s*(\\[\\s*]|null|\\{\\s*\\})\\s*$").containsMatchIn(r))) failedCode += codeKey("js", a.content)
        if (r.contains("This site blocked the browser")) siteFor(a).takeIf { it.isNotEmpty() }?.let { blockedSites += it }
    }

    private suspend fun autoExtract(): String {
        val ex = try { WebBrowser.extract(lastUserText().take(120)) } catch (_: Exception) { return "" }
        if (ex.startsWith("No tables")) return ""
        doneLookups += "extract|||" + WebBrowser.url
        return "Data extracted from this page automatically (tables/lists as rows; ▲/▼ = up/down shown by colour). " +
            "Use these rows; sort, filter or total them with <run lang=\"python\">:\n" + ex
    }

    /** A <js> scraper that came back empty or broken: hand over the page's data the easy way. */
    private suspend fun extractIfEmpty(a: FileAction.Browser, r: String): String {
        if (a.kind != "js") return ""
        val empty = r.startsWith("Browser error") || r.take(80).contains("returned nothing") ||
            Regex("^Script result:\\s*(\\[\\s*]|null|\\{\\s*\\})").containsMatchIn(r) || Regex("null").findAll(r).count() > 5
        if (!empty) return ""
        val ex = autoExtract()
        return if (ex.isNotEmpty()) "\n\nYour script didn't get the data. Here it is extracted for you:\n$ex" else ""
    }

    /** One line for an action card: the error, or the page the browser ended up on. */
    private fun short(result: String): String {
        val first = result.substringBefore('\n')
        if (first.startsWith("Browser error") || first.startsWith("Script result")) return result.take(200)
        val page = Regex("Browser page: (.*)").find(result)?.groupValues?.get(1)
        return if (page != null) "${first.trimEnd(':').replace(" Now", "")} - now on: ${page.take(120)}" else first.take(200)
    }

    /** The user asked to open/click something but the model answered without moving: send it back once. */
    private fun skippedNavigation(): Boolean {
        if (moved || moveNudged) return false
        var asked = ""
        var usedBrowser = false
        for (m in messages.asReversed()) {
            if (m.role == Role.USER) {
                asked = m.text
                usedBrowser = usedBrowser || (m.modelText ?: "").contains("looking at this page in your browser")
                break
            }
            if (m.role == Role.TOOL && Regex("^(Browser page|Data on the page|Web search results|Scrolled)").containsMatchIn(m.text)) usedBrowser = true
        }
        if (!usedBrowser || !navAsk.containsMatchIn(asked.take(400))) return false
        moveNudged = true
        messages += UiMessage(Role.TOOL, "The user asked you to open or click something, but you haven't opened, clicked or " +
            "navigated anything yet, so your answer is about the wrong page. Do that step first (<click n=\"...\"/> on the right " +
            "element, or <browse url=\"...\"/>), then answer from the new page.")
        return true
    }

    /** Warns when the answer has links, prices or percentages that appear in no page or output, or claims unproven success. */
    private fun checkClaims(reply: UiMessage) {
        val prose = proseOf(reply.text)
        val urlRe = Regex("https?://[^\\s)\\]\"'<>|*]+")
        val seen = messages.filter { it.role == Role.TOOL || it.role == Role.RUN || it.role == Role.USER }
            .joinToString(" ") { it.text + " " + (it.modelText ?: "") }.replace("&amp;", "&")
        val core = { u: String ->
            u.replace("&amp;", "&").replace(Regex("^https?://(www\\.)?"), "").substringBefore('?').substringBefore('#')
                .trimEnd('/', '.', ',', ';', ':').lowercase()
        }
        val links = urlRe.findAll(prose).map { it.value }.toSet()
        if (links.isNotEmpty()) {
            val seenCores = urlRe.findAll(seen).map { core(it.value) }.toSet()
            val madeUp = links.filter { core(it) !in seenCores }
            if (madeUp.isNotEmpty()) messages += UiMessage(Role.NOTE,
                "⚠ These links didn't appear in any page LocalForge opened, so they may be made up: " + madeUp.sorted().take(5).joinToString(", "))
        }
        val numRe = Regex("\\d(?:[\\d,]*\\d)?(?:\\.\\d+)?")
        val nums = { t: String -> numRe.findAll(t).mapNotNull { it.value.replace(",", "").toDoubleOrNull() }.toSet() }
        val seenNums = nums(seen)
        val odd = Regex("[£$€]\\s?\\d(?:[\\d,]*\\d)?(?:\\.\\d+)?|\\b\\d(?:[\\d,]*\\d)?(?:\\.\\d+)?\\s?%").findAll(prose)
            .map { it.value }.filter { !seenNums.containsAll(nums(it)) }.distinct().take(6).toList()
        if (odd.isNotEmpty()) messages += UiMessage(Role.NOTE,
            "⚠ These numbers didn't appear in any page or code output, so they may be estimates or made up: " + odd.joinToString(", "))
        if (!claims.containsMatchIn(prose)) return
        val since = mutableListOf<UiMessage>()
        for (m in messages.asReversed()) {
            if (m.role == Role.USER) break
            if (m.role == Role.RUN || m.role == Role.TOOL) since += m
        }
        val okRun = since.any { it.role == Role.RUN && it.text.startsWith("✓") }
        val okTool = since.any {
            it.role == Role.TOOL && Regex("^(Saved|Created|Typed and submitted|Did \\d+ of|Browser page|Clicked|Data |Fetched|Script result|Pressed)").containsMatchIn(it.text)
        }
        val runs = since.filter { it.role == Role.RUN }
        val failedLast = runs.isNotEmpty() && !runs.first().text.startsWith("✓") && !okTool
        if ((!okRun && !okTool) || failedLast) messages += UiMessage(Role.NOTE,
            "⚠ LocalForge couldn't confirm this: no successful run or saved file in this request matches what the answer says. Check the result before relying on it.")
    }

    private fun runAgent() {
        if (generating) return
        genJob = viewModelScope.launch {
            generating = true
            stopRequested = false
            try {
                while (stepsLeft > 0) {
                    stepsLeft--
                    val reply = UiMessage(Role.ASSISTANT, "")
                    messages += reply
                    val full = Engine.chat(history(), maxTokens, temperature) { piece ->
                        main.post { reply.text += piece }
                    }
                    reply.text = FileTools.stripThinking(full)
                    if (reply.text.isEmpty()) reply.text = if (stopRequested) "(stopped)" else "(no reply)"
                    reply.stats = "%.1f tokens/sec".format(Engine.lastTokensPerSecond)
                    if (looksBroken(reply.text)) {
                        messages += UiMessage(
                            Role.NOTE,
                            "This reply looks like gibberish. That usually means the model file is too heavily compressed " +
                                "(Q2/Q3) or broken. Download a Q4_K_M or Q4_0 version from the Models tab."
                        )
                        break
                    }
                    if (stopRequested) {
                        messages += UiMessage(Role.NOTE, "Stopped.")
                        break
                    }
                    totalSteps++
                    val flat = reply.text.replace(Regex("\\s+"), " ").trim()
                    if (flat == lastReply) {
                        repeats++
                        messages += UiMessage(Role.TOOL, "You sent exactly the same reply as before. Don't repeat it. Answer the user now with what you have.")
                        if (repeats >= 3) break
                        continue
                    }
                    lastReply = flat

                    var actions = FileTools.parse(full)
                    if (actions.none { it is FileAction.Run } && !ranCode && repeats == 0) {
                        // Only before any code ran: later, code blocks in a final answer are examples, not requests.
                        FileTools.fallbackRun(lastUserText(), full)?.let { actions = actions + it }
                    }
                    // Small models often finish a full answer and then ask for a needless click. Drop it.
                    var dropped = false
                    if (proseOf(full).length > 350 && actions.isNotEmpty() &&
                        actions.all { it is FileAction.Browser && it.kind in setOf("click", "type", "press", "scroll", "page") }) {
                        actions = emptyList(); dropped = true
                    }
                    if (actions.isEmpty() && !dropped && repeats == 0 && BrowserTools.anyTag.containsMatchIn(FileTools.stripThinking(full))) {
                        repeats++
                        messages += UiMessage(Role.TOOL, "A tool tag in your reply wasn't understood, so nothing was done. " +
                            "Write it exactly like the examples, e.g. <search query=\"words\"/>.")
                        continue
                    }
                    if (actions.isEmpty() && skippedNavigation()) continue
                    if (actions.isEmpty()) {
                        checkClaims(reply)
                        break
                    }

                    val browserAuto = autoApprove || browserAutoApprove
                    val results = mutableListOf<String>()
                    var continueLoop = false
                    var navigated = false   // a page opened in this reply: element numbers after it were guessed blind
                    var waitingCard: PendingAction? = null
                    for ((i, a0) in actions.withIndex()) {
                        // A card waits for the user: everything after it depends on it, so it runs after approval.
                        val wc = waitingCard
                        if (wc != null && !(a0 is FileAction.Browser && !a0.browserRead && (wc.action is FileAction.Batch || wc.action is FileAction.Browser))) {
                            wc.queued = actions.drop(i)
                            break
                        }
                        var a = a0
                        if (a is FileAction.Browser) {
                            if (a.kind == "browse" || a.kind == "fetch") a = fixUrl(a)
                            val key = "${a.kind}|${a.path}|${a.content}" + if (a.kind in setOf("extract", "page", "scroll")) "|" + WebBrowser.url else ""
                            val site = siteFor(a)
                            when {
                                site.isNotEmpty() && site in blockedSites -> {
                                    repeats++
                                    results += "Skipped (${a.label}): $site blocks the browser. Don't use that site again. Use another result or answer with what you have."
                                }
                                a.browserRead && key in doneLookups -> {
                                    repeats++
                                    results += "You already did this (${a.label}) and got the result above. Do not repeat it. Use what you have and answer the user now."
                                }
                                a.kind == "js" && codeKey("js", a.content) in failedCode -> {
                                    repeats++
                                    results += "That exact script already failed or the user said no to it. Don't run it again. Fix it differently, or answer with what you have."
                                }
                                navigated && a.kind in setOf("click", "type", "press", "js") ->
                                    results += "Skipped (${a.label}): you planned it before seeing the new page. Look at the page above and pick element numbers from it."
                                a.browserRead -> {
                                    if (a.kind == "browse" || a.kind == "search") navigated = true
                                    showBrowser()
                                    var r = browserStep(a)
                                    noteResult(a, r)
                                    if (a.kind == "browse" && dataWords.containsMatchIn(lastUserText()) && !r.contains("[LocalForge note]")) {
                                        r += "\n\n" + autoExtract()
                                    }
                                    if (a.kind == "search" || a.kind == "browse") {
                                        seenUrls += Regex("https?://[^\\s)\\]\"'<>]+").findAll(r).map { it.value }
                                    }
                                    results += r
                                    if (a.kind != "scroll") doneLookups += key
                                    continueLoop = true
                                }
                                browserAuto -> {
                                    val p = PendingAction(a)
                                    p.status = ActionStatus.DONE
                                    reply.actions += p
                                    showBrowser()
                                    var r = browserStep(a)
                                    r += extractIfEmpty(a, r)
                                    pageChanged()
                                    noteResult(a, r)
                                    p.result = short(r)
                                    results += r
                                    continueLoop = true
                                }
                                else -> {
                                    val last = reply.actions.lastOrNull()
                                    if (last != null && last === waitingCard && last.status == ActionStatus.PENDING &&
                                        (last.action is FileAction.Browser || last.action is FileAction.Batch)) {
                                        // Several clicks/typing in one reply: one card for all of them, run in order.
                                        val steps = when (val la = last.action) {
                                            is FileAction.Batch -> la.steps + a
                                            is FileAction.Browser -> listOf(la, a)
                                            else -> listOf(a)
                                        }
                                        val merged = PendingAction(FileAction.Batch(steps))
                                        reply.actions[reply.actions.size - 1] = merged
                                        waitingCard = merged
                                    } else {
                                        val p = PendingAction(a)
                                        reply.actions += p
                                        waitingCard = p
                                    }
                                }
                            }
                            continue
                        }
                        if (a.readOnly) {
                            results += if (!hasFileAccess) "No file access yet: the user must allow all-files access on the Home tab."
                            else withContext(Dispatchers.IO) { FileTools.execute(a) }
                            continueLoop = true
                            continue
                        }
                        if (a is FileAction.Run && codeKey(a.path, a.code) in failedCode) {
                            repeats++
                            results += "That exact code already failed or the user said no to it. Don't run it again. Fix it differently, or answer with what you have."
                            continueLoop = true
                            continue
                        }
                        val p = PendingAction(a)
                        reply.actions += p
                        if (a is FileAction.Run && autoApprove) {
                            p.status = ActionStatus.DONE
                            executeRun(a, p)
                            continueLoop = true
                        } else if (a !is FileAction.Run && autoApprove && a !is FileAction.Delete && hasFileAccess) {
                            p.status = ActionStatus.DONE
                            val r = withContext(Dispatchers.IO) { FileTools.execute(a) }
                            p.result = r
                            results += r
                            refreshFiles()
                            continueLoop = true
                        } else {
                            waitingCard = p
                        }
                    }
                    if (repeats >= 2 && stepsLeft > 1 && waitingCard == null) {
                        results += "STOP using tools now. Write your final answer to the user from the results above. " +
                            "If you couldn't get the information, say so and share what you did find."
                        stepsLeft = 1
                    }
                    if (results.isNotEmpty()) messages += UiMessage(Role.TOOL, results.joinToString("\n\n"))
                    if (waitingCard != null || !continueLoop) break
                    if (stepsLeft == 0) messages += UiMessage(Role.NOTE, "Paused after several steps. Say \"continue\" to keep going.")
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                messages += UiMessage(Role.NOTE, e.message ?: "Something went wrong")
            } finally {
                WebBrowser.step = null
                generating = false
                saveChat()
                val app = getApplication<LocalForgeApp>()
                if (!app.visible) WorkService.notifyDone(app, "Your reply is ready — tap to view")
            }
        }
    }

    private fun messageOf(p: PendingAction): UiMessage? = messages.lastOrNull { m -> m.actions.any { it === p } }

    /** After a card is answered: run the steps queued behind it, then let the model carry on. */
    private suspend fun afterCard(p: PendingAction) {
        val outs = mutableListOf<String>()
        for (q in p.queued) {
            if (q is FileAction.Browser && q.browserRead) outs += browserStep(q)
            else if (q.readOnly && hasFileAccess) outs += withContext(Dispatchers.IO) { FileTools.execute(q) }
            else { outs += "Not done yet: ${q.label}. Ask for it again if you still need it."; break }
        }
        p.queued = emptyList()
        if (outs.isNotEmpty()) messages += UiMessage(Role.TOOL, outs.joinToString("\n\n"))
        val stillWaiting = messageOf(p)?.actions?.any { it.status == ActionStatus.PENDING } == true
        if (Engine.isLoaded && !stillWaiting) {
            if (stepsLeft <= 0 && totalSteps < 24) stepsLeft = 3
            runAgent()
        }
    }

    fun approve(p: PendingAction) {
        if (p.status != ActionStatus.PENDING) return
        val a = p.action
        if (generating) {
            p.result = "Wait for the current reply to finish."
            return
        }
        if (a is FileAction.Run) {
            p.status = ActionStatus.DONE
            viewModelScope.launch {
                executeRun(a, p)
                afterCard(p)
            }
            return
        }
        if (a is FileAction.Browser || a is FileAction.Batch) {
            p.status = ActionStatus.DONE
            viewModelScope.launch {
                generating = true
                try {
                    showBrowser()
                    if (a is FileAction.Batch) {
                        val outs = mutableListOf<String>()
                        for (st in a.steps) {
                            var r = browserStep(st)
                            r += extractIfEmpty(st, r)
                            noteResult(st, r)
                            if (r.startsWith("Browser error")) { outs += "${st.label}: $r"; break }
                            outs += "${st.label}: done"
                        }
                        p.result = "Did ${outs.size} of ${a.steps.size} steps"
                        val page = try { WebBrowser.describe(2500) } catch (e: Exception) { "(couldn't read the page: ${e.message})" }
                        messages += UiMessage(Role.TOOL, outs.joinToString("\n") + "\n\nThe page now (check it shows what you expected):\n" + page)
                    } else if (a is FileAction.Browser) {
                        var r = browserStep(a)
                        r += extractIfEmpty(a, r)
                        noteResult(a, r)
                        p.result = short(r)
                        messages += UiMessage(Role.TOOL, r)
                    }
                    pageChanged()
                } catch (e: Exception) {
                    messages += UiMessage(Role.NOTE, e.message ?: "Browser step failed")
                } finally {
                    generating = false
                }
                afterCard(p)
            }
            return
        }
        if (!hasFileAccess) {
            p.result = "Grant all-files access on the Files tab first."
            return
        }
        p.status = ActionStatus.DONE
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) { FileTools.execute(a) }
            p.result = r
            messages += UiMessage(Role.TOOL, r)
            refreshFiles()
            pageChanged()
            afterCard(p)
        }
    }

    /** ▶ Run button on a code block: runs it and shows the output (the model sees it next turn). */
    fun runSnippet(lang: String, code: String) {
        viewModelScope.launch {
            executeRun(FileAction.Run(FileTools.normalizeLang(lang), code), null)
        }
    }

    fun reject(p: PendingAction) {
        if (p.status != ActionStatus.PENDING) return
        p.status = ActionStatus.REJECTED
        when (val a = p.action) {
            is FileAction.Run -> failedCode += codeKey(a.path, a.code)
            is FileAction.Browser -> if (a.kind == "js") failedCode += codeKey("js", a.content)
            is FileAction.Batch -> a.steps.filter { it.kind == "js" }.forEach { failedCode += codeKey("js", it.content) }
            else -> {}
        }
        p.queued = emptyList()
        val stillWaiting = messageOf(p)?.actions?.any { it.status == ActionStatus.PENDING } == true
        if (generating || !Engine.isLoaded || stillWaiting) return
        // Tell the model, so it answers with what it has instead of the turn ending silently.
        messages += UiMessage(Role.TOOL, "The user said no to: ${p.action.label}. Don't ask for it again. Answer the user now with what you already found.")
        stepsLeft = maxOf(stepsLeft, 2)
        runAgent()
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

    // ---- Background work + saved chat ----
    var runningCode by mutableStateOf(false)
        private set
    private val chatFile get() = File(getApplication<Application>().filesDir, "chat.json")

    /** Saves the conversation so it survives the app being closed or restarted. */
    fun saveChat() {
        val arr = JSONArray()
        for (m in messages) {
            if (m.role == Role.ASSISTANT && m.text.isBlank()) continue
            arr.put(JSONObject().apply {
                put("role", m.role.name)
                put("text", m.text)
                m.modelText?.let { put("model", it) }
                m.stats?.let { put("stats", it) }
            })
        }
        try { chatFile.writeText(arr.toString()) } catch (_: Exception) {}
    }

    private fun loadChat() {
        try {
            if (!chatFile.exists()) return
            val arr = JSONArray(chatFile.readText())
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                val role = runCatching { Role.valueOf(o.getString("role")) }.getOrNull() ?: continue
                val m = UiMessage(role, o.getString("text"), if (o.has("model")) o.getString("model") else null)
                if (o.has("stats")) m.stats = o.getString("stats")
                messages += m
            }
        } catch (_: Exception) {}
    }

    private fun watchBackgroundWork() {
        viewModelScope.launch {
            snapshotFlow {
                val dl = downloads.values.filter { it.error == null }
                when {
                    generating -> "Thinking…"
                    runningCode -> "Running code…"
                    loading -> "Loading model…"
                    dl.isNotEmpty() -> {
                        val d = dl.first()
                        val pct = if (d.total > 0) (d.done * 100 / d.total).toInt() else 0
                        "Downloading ${d.file.fileName} · $pct%"
                    }
                    else -> null
                }
            }.distinctUntilChanged().collect { status ->
                val app = getApplication<Application>()
                if (status != null) WorkService.start(app, status) else WorkService.stop(app)
            }
        }
    }

    init {
        WebBrowser.init(app)
        refreshPermission()
        refreshModels()
        device = store.deviceInfo()
        refreshTermux()
        loadChat()
        watchBackgroundWork()
    }

    /** Heuristic: real replies are mostly letters and spaces; broken models emit digits and symbols. */
    private fun looksBroken(text: String): Boolean {
        if (text.length < 40) return false
        val prose = text.replace(Regex("```[\\s\\S]*?(```|$)"), "")
        if (prose.length < 40) return false
        val letters = prose.count { it.isLetter() || it == ' ' }
        return letters.toDouble() / prose.length < 0.55
    }
}
