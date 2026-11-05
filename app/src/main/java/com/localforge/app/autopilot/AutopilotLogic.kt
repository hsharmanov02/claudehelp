package com.localforge.app.autopilot

import kotlin.math.max
import kotlin.math.min

/** A fact the AI saved for later rounds with <note>...</note>. */
data class Note(val text: String, val time: String)

/** One line per round: what it did and what comes next. */
data class JournalEntry(val round: Int, val time: String, val summary: String)

/**
 * The parts of the Autopilot that don't need Android: the round prompt, notes, summaries and back-off.
 * Kept separate so they're covered by plain unit tests (app/src/test).
 */
object AutopilotLogic {
    /** Pauses between rounds the screen offers, in seconds. */
    val PAUSES = listOf(0, 30, 60, 300, 900, 1800, 3600)
    const val MAX_MEMORY = 60
    const val MAX_JOURNAL = 300
    /** Phones usually run 4096-token contexts, so the prompt only carries the newest notes and rounds. */
    const val PROMPT_MEMORY = 20
    const val PROMPT_MEMORY_CHARS = 1800
    const val PROMPT_JOURNAL = 5
    const val ROUND_TIMEOUT_MS = 50 * 60_000L
    const val KEEP_MESSAGES = 120

    private val noteTag = Regex("<(note|remember)>([\\s\\S]*?)</\\1>", RegexOption.IGNORE_CASE)
    private val summaryLine = Regex("round summary\\s*[:\\-]\\s*(.+)", RegexOption.IGNORE_CASE)
    private val idle = Regex("nothing (useful |more |else |new )?to do", RegexOption.IGNORE_CASE)
    private val anyTag = Regex("<(write|run|type|js|mod|note|remember)\\b[\\s\\S]*?(</\\1>|$)|<[a-z]+\\b[^>]*/?>", RegexOption.IGNORE_CASE)

    fun clean(text: String, max: Int = 300): String = text.replace(Regex("\\s+"), " ").trim().take(max)

    /** Everything the reply saved with <note>...</note> (or <remember>). */
    fun notes(reply: String): List<String> =
        noteTag.findAll(reply).map { clean(it.groupValues[2]) }.filter { it.isNotEmpty() }.toList()

    /** The "Round summary:" line, or the reply's own words if the model forgot it. */
    fun summary(lastReply: String): String {
        summaryLine.findAll(lastReply).lastOrNull()?.let { return clean(it.groupValues[1].replace(anyTag, "")) }
        val prose = clean(lastReply.replace(anyTag, " "))
        return prose.ifEmpty { "(no reply)" }
    }

    fun isIdle(summary: String): Boolean = idle.containsMatchIn(summary)

    /** Nothing to do: wait longer each time (up to an hour) until something changes. */
    fun nextPause(baseSec: Int, idleRounds: Int): Int =
        if (idleRounds <= 0) baseSec else min(max(baseSec, 60) * (1 shl min(idleRounds, 10)), 3600)

    /** After a model failed to load: 2, 4, 8 … minutes, at most an hour. */
    fun loadBackoffSec(fails: Int): Int = min(60 * (1 shl min(max(fails, 1), 10)), 3600)

    /** Adds a note, dropping an older copy of the same text and the oldest notes past the limit. */
    fun addNote(memory: List<Note>, text: String, time: String): List<Note> {
        val t = clean(text)
        if (t.isEmpty()) return memory
        return (memory.filter { !it.text.equals(t, ignoreCase = true) } + Note(t, time)).takeLast(MAX_MEMORY)
    }

    fun prompt(goal: String, round: Int, now: String, memory: List<Note>, journal: List<JournalEntry>, browserLine: String = ""): String {
        val lines = mutableListOf(
            "[Standing order from the user - autopilot round $round, $now]",
            "Main command (it never ends; keep working toward it): ${goal.trim()}",
            "",
            "Your long-term memory from earlier rounds (it survives restarts):"
        )
        // Newest notes first until the budget is used, then shown oldest first.
        val picked = ArrayList<Note>()
        var used = 0
        for (n in memory.takeLast(PROMPT_MEMORY).asReversed()) {
            if (used + n.text.length > PROMPT_MEMORY_CHARS && picked.isNotEmpty()) break
            picked += n
            used += n.text.length
        }
        if (picked.isEmpty()) lines += "- (empty: this is the first round)" else picked.asReversed().forEach { lines += "- ${it.text}" }
        val jr = journal.takeLast(PROMPT_JOURNAL)
        if (jr.isNotEmpty()) {
            lines += ""
            lines += "What the last rounds did, oldest first:"
            jr.forEach { lines += "- Round ${it.round} (${it.time.takeLast(5)}): ${it.summary}" }
        }
        if (browserLine.isNotBlank()) {
            lines += ""
            lines += browserLine
        }
        lines += ""
        lines += "Do the next useful step toward the main command now, using the browser and your other tools. " +
            "Don't redo what your memory says is done: pick up where the last round stopped."
        lines += "Save everything later rounds will need (findings, links, progress, what to do next) with <note>...</note>."
        lines += "End this round with one line starting \"Round summary:\" saying what you did and what comes next. " +
            "If there is nothing useful to do right now, reply \"Round summary: nothing to do right now\" and stop."
        return lines.joinToString("\n")
    }

    fun dur(sec: Long): String = when {
        sec < 90 -> "$sec s"
        sec < 5400 -> "${(sec + 30) / 60} min"
        else -> "%.1f h".format(sec / 3600.0)
    }
}
