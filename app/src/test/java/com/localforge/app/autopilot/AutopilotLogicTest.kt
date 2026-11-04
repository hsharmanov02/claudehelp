package com.localforge.app.autopilot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AutopilotLogicTest {
    @Test fun notesAreCollected() {
        val r = "Found it. <note>Best price so far: £12 at shop A</note> and <remember>Check shop B next</remember>"
        assertEquals(listOf("Best price so far: £12 at shop A", "Check shop B next"), AutopilotLogic.notes(r))
    }

    @Test fun summaryLineWins() {
        val r = "I opened the page.\nRound summary: checked 3 shops, next compare delivery <note>x</note>"
        assertEquals("checked 3 shops, next compare delivery", AutopilotLogic.summary(r))
    }

    @Test fun summaryFallsBackToProse() {
        assertEquals("Looked at the news.", AutopilotLogic.summary("<page/> Looked at the news."))
        assertEquals("(no reply)", AutopilotLogic.summary("<page/>"))
    }

    @Test fun idleDetection() {
        assertTrue(AutopilotLogic.isIdle("nothing to do right now"))
        assertTrue(AutopilotLogic.isIdle("Nothing new to do until tomorrow"))
        assertFalse(AutopilotLogic.isIdle("did three things"))
    }

    @Test fun pausesBackOffAndCap() {
        assertEquals(60, AutopilotLogic.nextPause(60, 0))
        assertEquals(120, AutopilotLogic.nextPause(60, 1))
        assertEquals(120, AutopilotLogic.nextPause(0, 1))
        assertEquals(3600, AutopilotLogic.nextPause(600, 5))
        assertEquals(120, AutopilotLogic.loadBackoffSec(1))
        assertEquals(3600, AutopilotLogic.loadBackoffSec(30))
    }

    @Test fun notesDeduplicateAndCap() {
        var m = emptyList<Note>()
        repeat(AutopilotLogic.MAX_MEMORY + 5) { m = AutopilotLogic.addNote(m, "fact $it", "t") }
        assertEquals(AutopilotLogic.MAX_MEMORY, m.size)
        m = AutopilotLogic.addNote(m, "FACT 10", "t2")
        assertEquals(1, m.count { it.text.equals("fact 10", true) })
        assertEquals("FACT 10", m.last().text)
    }

    @Test fun promptCarriesGoalMemoryAndJournal() {
        val p = AutopilotLogic.prompt(
            "watch prices", 4, "Tue 2026-11-04 10:00",
            listOf(Note("price was £10", "t")), listOf(JournalEntry(3, "2026-11-04 09:00", "checked"))
        )
        assertTrue(p.contains("round 4"))
        assertTrue(p.contains("watch prices"))
        assertTrue(p.contains("- price was £10"))
        assertTrue(p.contains("Round 3 (09:00): checked"))
        assertTrue(p.contains("Round summary:"))
    }

    @Test fun promptStaysSmallForPhoneContexts() {
        val mem = (1..60).map { Note("x".repeat(290) + it, "t") }
        val p = AutopilotLogic.prompt("goal", 1, "now", mem, emptyList())
        assertTrue("prompt was ${p.length} chars", p.length < 3200)
        assertTrue(p.contains("x60"))   // the newest note is always kept
    }
}
