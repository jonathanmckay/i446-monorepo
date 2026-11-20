package com.mckay.neg1n.wear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

/** The 30-minute -1n nudge: buzz once when a block is 30 minutes old and a
 * ritual is still open, stay silent when all five are done, and never guess
 * from a status that is old or belongs to another block. */
class NudgeLogicTest {

    private val zone = ZoneId.of("America/Los_Angeles")
    private val allTags = listOf("سمش", "-1g", "-1ibx", "-1t", "-1l")

    private fun at(hour: Int, minute: Int, day: Int = 19) =
        ZonedDateTime.of(2026, 11, day, hour, minute, 0, 0, zone).toInstant().toEpochMilli()

    private fun status(block: String?, done: Set<String>, updatedAt: Long) =
        Neg1nStatus(block, done, allTags - done, updatedAt, endpoint = null)

    @Test
    fun `nudges with the open rituals in bar order at 30 minutes`() {
        val now = at(8, 30)
        val d = NudgeLogic.decide(now, status("巳", setOf("-1g", "سمش"), now - 60_000), zone)
        assertEquals(NudgeLogic.Decision.Nudge("巳", listOf("-1ibx", "-1t", "-1l")), d)
    }

    @Test
    fun `silent when all five are done`() {
        val now = at(8, 31)
        assertEquals(NudgeLogic.Decision.AllDone,
            NudgeLogic.decide(now, status("巳", allTags.toSet(), now - 60_000), zone))
    }

    @Test
    fun `not due before 30 minutes or after the 15-minute window`() {
        val open = status("巳", emptySet(), at(8, 29))
        assertEquals(NudgeLogic.Decision.NotDue, NudgeLogic.decide(at(8, 29), open, zone))
        assertTrue(NudgeLogic.decide(at(8, 44), open.copy(updatedAtMillis = at(8, 40)), zone) is NudgeLogic.Decision.Nudge)
        assertEquals(NudgeLogic.Decision.NotDue,
            NudgeLogic.decide(at(8, 45), open.copy(updatedAtMillis = at(8, 44)), zone))
    }

    @Test
    fun `a status for the previous block is stale, not five open rituals`() {
        val now = at(10, 30)
        assertEquals(NudgeLogic.Decision.Stale,
            NudgeLogic.decide(now, status("巳", emptySet(), now - 60_000), zone))
    }

    @Test
    fun `a status older than 20 minutes is stale`() {
        val now = at(8, 30)
        assertEquals(NudgeLogic.Decision.Stale,
            NudgeLogic.decide(now, status("巳", emptySet(), at(8, 9)), zone))
        assertTrue(NudgeLogic.decide(now, status("巳", emptySet(), at(8, 11)), zone) is NudgeLogic.Decision.Nudge)
    }

    @Test
    fun `no block and no nudge outside 0400-2200`() {
        assertNull(NudgeLogic.slotAt(at(3, 59), zone))
        assertNull(NudgeLogic.slotAt(at(22, 0), zone))
        assertEquals("亥", NudgeLogic.slotAt(at(21, 59), zone)?.block)
        assertEquals("卯", NudgeLogic.slotAt(at(4, 0), zone)?.block)
        assertEquals(NudgeLogic.Decision.NotDue,
            NudgeLogic.decide(at(22, 30), status(null, emptySet(), at(22, 29)), zone))
    }

    @Test
    fun `the alarm walks presync then nudge and rolls over to tomorrow`() {
        assertEquals(at(8, 25), NudgeLogic.nextWake(at(8, 0), zone))
        assertEquals(at(8, 30), NudgeLogic.nextWake(at(8, 25), zone))
        assertEquals(at(10, 25), NudgeLogic.nextWake(at(8, 30), zone))
        assertEquals(at(20, 30), NudgeLogic.nextWake(at(20, 27), zone))
        assertEquals(at(4, 25, day = 20), NudgeLogic.nextWake(at(20, 30), zone))
    }

    @Test
    fun `blocks follow Los Angeles time whatever zone the watch is in`() {
        // 08:30 in LA is 11:30 in New York: still 巳, 30 minutes in.
        val now = ZonedDateTime.of(2026, 11, 19, 11, 30, 0, 0, ZoneId.of("America/New_York"))
            .toInstant().toEpochMilli()
        assertEquals("巳", NudgeLogic.slotAt(now)?.block)
        assertEquals(now, NudgeLogic.slotAt(now)?.nudgeAt)
    }
}
