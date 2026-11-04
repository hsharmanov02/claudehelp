package com.localforge.app.autopilot

import android.content.Context
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.localforge.app.LocalForgeApp
import com.localforge.app.Role
import com.localforge.app.agent.WebBrowser
import com.localforge.app.engine.Engine
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.coroutines.coroutineContext
import kotlin.math.abs

/**
 * LocalForge Autopilot: one main command the AI keeps working on, round after round, 24/7, until STOP.
 *
 * Every round it gets the command, its long-term memory and the last rounds' summaries, then works through the
 * normal chat loop (browser, files, code). Facts it saves with <note> go into the memory, the "Round summary:" line
 * into the journal. Both are saved after every change, so they survive the app being killed, crashes and reboots.
 *
 * Staying alive: [AutopilotService] is a foreground service that Android restarts if it kills the app
 * (START_STICKY), and [Watchdog] checks every 15 minutes that the service and this loop are still going.
 * After the phone restarts nothing runs until the user says so ([awaitingConsent]).
 */
object Autopilot {
    private lateinit var app: LocalForgeApp
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var loopJob: Job? = null

    // ---- saved state ----
    var enabled by mutableStateOf(false)
        private set
    var goal by mutableStateOf("")
        private set
    var pauseSec by mutableStateOf(60)
        private set
    var round by mutableStateOf(0)
        private set
    var modelName by mutableStateOf<String?>(null)
        private set
    var errors by mutableStateOf(0)
        private set
    var restarts by mutableStateOf(0)
        private set
    /** The phone restarted while a task was running: ask before loading the model, do nothing until then. */
    var awaitingConsent by mutableStateOf(false)
        private set
    var startedAt by mutableStateOf(0L)
        private set
    val memory = mutableStateListOf<Note>()
    val journal = mutableStateListOf<JournalEntry>()
    private var bootTime = 0L

    // ---- live state ----
    var status by mutableStateOf("Off")
        private set
    var inRound by mutableStateOf(false)
        private set
    var nextAt by mutableStateOf(0L)
        private set
    /** Last sign of life from the loop, for the watchdog. */
    @Volatile var lastBeat = 0L
        private set
    private var idleRounds = 0
    private var loadFails = 0
    private var approvalNotified = false

    private val initialized get() = ::app.isInitialized
    private val folder get() = File(app.filesDir, "autopilot").also { it.mkdirs() }
    private val stateFile get() = File(folder, "autopilot.json")

    private fun now() = System.currentTimeMillis()
    private fun stamp(t: Long = now()) = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date(t))

    /** When this boot of the phone started (to tell a reboot from the app merely being restarted). */
    private fun currentBootTime() = now() - SystemClock.elapsedRealtime()

    @Synchronized
    fun init(ctx: Context) {
        if (initialized) return
        app = ctx.applicationContext as LocalForgeApp
        load()
        if (enabled) {
            if (abs(bootTime - currentBootTime()) > 2 * 60_000L) {
                awaitingConsent = true
                status = "Your phone restarted. Waiting for you to say Continue."
            } else if (!awaitingConsent) {
                // Same boot: Android killed or restarted the app (crash, memory, update). Carry on.
                restarts++
                nextAt = now() + 5_000
                status = "Resuming after the app was restarted…"
            }
            save()
        } else if (goal.isNotBlank()) {
            status = "Stopped. Press Continue to pick it up again, or start a new task."
        }
    }

    // ---------- saving ----------

    private fun load() {
        try {
            if (!stateFile.exists()) return
            val o = JSONObject(stateFile.readText())
            enabled = o.optBoolean("enabled")
            goal = o.optString("goal")
            pauseSec = o.optInt("pause", 60)
            round = o.optInt("round")
            modelName = o.optString("model").ifBlank { null }
            errors = o.optInt("errors")
            restarts = o.optInt("restarts")
            awaitingConsent = o.optBoolean("awaitingConsent")
            bootTime = o.optLong("bootTime")
            startedAt = o.optLong("startedAt")
            o.optJSONArray("memory")?.let { a ->
                for (i in 0 until a.length()) a.getJSONObject(i).let { memory += Note(it.optString("text"), it.optString("time")) }
            }
            o.optJSONArray("journal")?.let { a ->
                for (i in 0 until a.length()) a.getJSONObject(i).let { journal += JournalEntry(it.optInt("round"), it.optString("time"), it.optString("summary")) }
            }
        } catch (_: Exception) {}
    }

    private fun toJson(): JSONObject = JSONObject().apply {
        put("enabled", enabled); put("goal", goal); put("pause", pauseSec); put("round", round)
        put("model", modelName ?: ""); put("errors", errors); put("restarts", restarts)
        put("awaitingConsent", awaitingConsent); put("bootTime", bootTime); put("startedAt", startedAt)
        put("memory", JSONArray().apply { memory.forEach { put(JSONObject().put("text", it.text).put("time", it.time)) } })
        put("journal", JSONArray().apply { journal.forEach { put(JSONObject().put("round", it.round).put("time", it.time).put("summary", it.summary)) } })
    }

    /** Written to a temp file and renamed, so a power cut mid-write never leaves a broken file. */
    @Synchronized
    private fun save() {
        try {
            val tmp = File(folder, "autopilot.json.tmp")
            tmp.writeText(toJson().toString(1))
            if (!tmp.renameTo(stateFile)) { stateFile.delete(); tmp.renameTo(stateFile) }
        } catch (_: Exception) {}
    }

    // ---------- controls ----------

    /** Starts [newGoal] as a fresh task. The old task's memory and journal are archived first. */
    fun newTask(newGoal: String, model: String?, pause: Int): String? {
        if (newGoal.isBlank()) return "Write the main command first."
        archive()
        memory.clear(); journal.clear()
        round = 0; errors = 0; restarts = 0
        goal = newGoal.trim()
        startedAt = now()
        return begin(model, pause)
    }

    /** Picks the stopped task up again with all its memory. */
    fun continueTask(model: String?, pause: Int): String? {
        if (goal.isBlank()) return "There's no task to continue. Start a new one."
        return begin(model, pause)
    }

    private fun begin(model: String?, pause: Int): String? {
        modelName = model ?: modelName
        pauseSec = pause.coerceIn(0, 24 * 3600)
        enabled = true
        awaitingConsent = false
        bootTime = currentBootTime()
        idleRounds = 0; loadFails = 0
        nextAt = now()
        status = "Starting…"
        save()
        AutopilotService.start(app)
        Watchdog.schedule(app)
        return null
    }

    /** The red STOP button: ends the run, unloads the model and stops the background service. */
    fun stop() {
        enabled = false
        awaitingConsent = false
        save()
        loopJob?.cancel()
        loopJob = null
        inRound = false
        val vm = app.state
        vm.stopGenerating()
        scope.launch {
            delay(1500)
            if (!enabled) vm.unloadModel()
        }
        status = "Stopped. Press Continue to pick it up again, or start a new task."
        AutopilotService.stop(app)
        Watchdog.cancel(app)
        AutopilotNotify.cancelAsk(app)
    }

    /** The answer to "Load the model and continue?" after the phone restarted. */
    fun consent(yes: Boolean) {
        AutopilotNotify.cancelAsk(app)
        if (!awaitingConsent) {
            if (yes && enabled) AutopilotService.start(app)
            return
        }
        awaitingConsent = false
        if (yes) {
            bootTime = currentBootTime()
            nextAt = now()
            status = "Continuing after the restart…"
            save()
            AutopilotService.start(app)
            Watchdog.schedule(app)
        } else {
            enabled = false
            status = "Not continued after the restart. Press Continue whenever you want."
            save()
            AutopilotService.stop(app)
            Watchdog.cancel(app)
        }
    }

    fun setPause(sec: Int) { pauseSec = sec.coerceIn(0, 24 * 3600); save() }
    fun setModel(name: String?) { modelName = name; save() }

    fun runNow() { idleRounds = 0; nextAt = now() }

    fun forget(index: Int) {
        if (index in memory.indices) { memory.removeAt(index); save() }
    }

    private fun remember(text: String) {
        val updated = AutopilotLogic.addNote(memory.toList(), text, stamp())
        memory.clear(); memory.addAll(updated)
        save()
    }

    private fun addJournal(summary: String, n: Int = round) {
        journal += JournalEntry(n, stamp(), AutopilotLogic.clean(summary))
        while (journal.size > AutopilotLogic.MAX_JOURNAL) journal.removeAt(0)
        save()
    }

    private fun archive() {
        if (goal.isBlank() && memory.isEmpty() && journal.isEmpty()) return
        try {
            val dir = File(folder, "history").also { it.mkdirs() }
            val name = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date()) + ".json"
            File(dir, name).writeText(toJson().toString(1))
        } catch (_: Exception) {}
    }

    // ---------- the loop (runs while AutopilotService is alive) ----------

    val loopAlive: Boolean get() = loopJob?.isActive == true

    fun attachLoop() {
        if (loopAlive) return
        lastBeat = now()
        loopJob = scope.launch { loop() }
    }

    fun detachLoop() {
        loopJob?.cancel()
        loopJob = null
        inRound = false
    }

    /** The watchdog found the loop silent for too long: stop whatever hung and start it again. */
    fun restartLoop() {
        detachLoop()
        try { Engine.stop() } catch (_: Exception) {}
        errors++
        addJournal("The autopilot stopped responding and was restarted.")
        attachLoop()
    }

    private fun showStatus(s: String) {
        if (s == status) return
        status = s
        AutopilotService.update(app, s)
    }

    private suspend fun loop() {
        while (coroutineContext.isActive) {
            lastBeat = now()
            try {
                tick()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Throwable) {
                // Never let one bad round end the autopilot.
                errors++
                addJournal("Round failed: ${e.message ?: e.javaClass.simpleName}")
                nextAt = now() + maxOf(60, pauseSec) * 1000L
                showStatus("Last round failed (${e.message}). Trying again soon.")
            } finally {
                inRound = false
            }
            delay(2000)
        }
    }

    private suspend fun tick() {
        if (!enabled) { showStatus("Off"); return }
        if (awaitingConsent) { showStatus("Your phone restarted. Waiting for you to say Continue."); return }
        val vm = app.state
        when {
            vm.loading -> { showStatus("Waiting: a model is loading…"); return }
            vm.generating -> { showStatus("Waiting: the AI is busy with a chat message…"); return }
            vm.hasPendingCard() -> {
                showStatus("Waiting for your approval in the Code tab.")
                if (!approvalNotified) { approvalNotified = true; AutopilotNotify.attention(app, "The AI is waiting for your approval in the Code tab.") }
                return
            }
        }
        approvalNotified = false
        val left = nextAt - now()
        if (left > 0) { showStatus("Next round in ${AutopilotLogic.dur((left + 999) / 1000)}"); return }
        if (!ensureModel()) return
        runRound()
    }

    private suspend fun ensureModel(): Boolean {
        val vm = app.state
        if (Engine.isLoaded && (modelName == null || vm.loadedModel == modelName)) { loadFails = 0; return true }
        vm.refreshModels()
        if (vm.localModels.isEmpty()) {
            showStatus("Waiting: no models found. Allow all-files access, or download one in Models.")
            nextAt = now() + 30_000
            return false
        }
        showStatus("Loading ${(modelName ?: vm.lastModelName ?: "the model").removeSuffix(".gguf")}…")
        if (vm.ensureModel(modelName)) {
            loadFails = 0
            if (modelName == null) { modelName = vm.loadedModel; save() }
            return true
        }
        loadFails++
        val wait = AutopilotLogic.loadBackoffSec(loadFails)
        nextAt = now() + wait * 1000L
        showStatus("Couldn't load the model (${vm.engineMessage ?: "unknown error"}). Trying again in ${AutopilotLogic.dur(wait.toLong())}.")
        return false
    }

    private suspend fun runRound() {
        val vm = app.state
        round++
        val n = round
        save()
        inRound = true
        showStatus("Round $n running…")
        val browserLine = if (WebBrowser.hasPage) "The browser is on: ${WebBrowser.title} (${WebBrowser.url})" else ""
        val prompt = AutopilotLogic.prompt(goal, n, SimpleDateFormat("EEEE yyyy-MM-dd HH:mm", Locale.US).format(Date()),
            memory.toList(), journal.toList(), browserLine)
        val start = vm.messages.size
        if (!vm.sendAuto(prompt, "🎯 Autopilot round $n: $goal")) {
            round--
            inRound = false
            save()
            return
        }
        val t0 = now()
        delay(500)
        while (vm.generating || vm.loading) {
            lastBeat = now()
            if (!enabled) return
            if (now() - t0 > AutopilotLogic.ROUND_TIMEOUT_MS) {
                vm.stopGenerating()
                addJournal("Stopped: the round took longer than 30 minutes.", n)
                break
            }
            delay(1000)
        }
        val replies = vm.messages.drop(start).filter { it.role == Role.ASSISTANT && it.text.isNotBlank() }
        replies.forEach { r -> AutopilotLogic.notes(r.text).forEach { remember(it) } }
        val summary = AutopilotLogic.summary(replies.lastOrNull()?.text ?: "")
        addJournal(summary, n)
        idleRounds = if (AutopilotLogic.isIdle(summary)) idleRounds + 1 else 0
        val pause = AutopilotLogic.nextPause(pauseSec, idleRounds)
        nextAt = now() + pause * 1000L
        vm.pruneMessages(AutopilotLogic.KEEP_MESSAGES)
        vm.saveChat()
        inRound = false
        showStatus(
            if (vm.hasPendingCard()) "Waiting for your approval in the Code tab."
            else "Round $n done. Next round in ${AutopilotLogic.dur(pause.toLong())}."
        )
    }
}
