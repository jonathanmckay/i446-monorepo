package com.mckay.neg1n.wear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Regression coverage for the swipe-to-complete "it just reappears" bug.
 *
 * 2026-09-08: RitualListActivity's 3s reconcile reload() raced the phone's
 * real completion work and read stale Data Layer state.
 * 2026-09-20: the list-open "sync now" push could land AFTER the completion
 * push and revert the tag, so a reopened list (fresh activity) and the
 * complication bar showed the ritual not-done again until the next
 * periodic sync — the in-memory, activity-lifetime overlay didn't cover
 * either. The overlay is now persisted and applied to the status itself. */
class PendingCompletionsTest {

    private val allTags = listOf("سمش", "-1g", "-1ibx", "-1t", "-1l")

    private fun status(block: String? = "巳", done: Set<String> = emptySet(), notDone: List<String> = allTags) =
        Neg1nStatus(block, done, notDone, updatedAtMillis = 1_000L, endpoint = null)

    @Test
    fun `a swiped tag renders done in the list and the bar while the synced item is stale`() {
        val pending = PendingCompletions(now = { 10_000L })
        pending.markCompleted("-1t", block = "巳")

        val overlaid = pending.applyTo(status())

        assertEquals(allTags - "-1t", overlaid.notDone)
        // BarRenderer colors from `done`, so the tag must move INTO done,
        // not merely drop out of notDone.
        assertTrue("-1t" in overlaid.done)
    }

    @Test
    fun `a swipe survives a stale late push and a reopened list`() {
        // Persistence round trip = what a fresh RitualListActivity instance
        // (or the complication service) reads back from SharedPreferences.
        var stored = ""
        val first = PendingCompletions(now = { 10_000L }, persist = { stored = PendingCompletions.encode(it) })
        first.markCompleted("-1t", block = "巳")

        val reopened = PendingCompletions(initial = PendingCompletions.decode(stored), now = { 15_000L })
        val staleRevert = status() // synced item still lists -1t as not done
        assertEquals(allTags - "-1t", reopened.applyTo(staleRevert).notDone)
    }

    @Test
    fun `the overlay drops once the remote confirms the tag done`() {
        val pending = PendingCompletions(now = { 10_000L })
        pending.markCompleted("-1t", block = "巳")

        val confirmed = status(done = setOf("-1t"), notDone = allTags - "-1t")
        assertEquals(confirmed, pending.applyTo(confirmed))
        assertTrue(pending.pendingFor(confirmed).isEmpty())
    }

    @Test
    fun `a swipe in one block never hides the next block's ritual`() {
        val pending = PendingCompletions(now = { 10_000L })
        pending.markCompleted("-1t", block = "巳")

        val nextBlock = status(block = "午")
        assertEquals(allTags, pending.applyTo(nextBlock).notDone)
    }

    @Test
    fun `a swipe whose completion never landed expires after the TTL`() {
        var clock = 10_000L
        val pending = PendingCompletions(now = { clock }, ttlMillis = 1_000L)
        pending.markCompleted("-1t", block = "巳")

        clock = 10_500L
        assertEquals(allTags - "-1t", pending.applyTo(status()).notDone)
        clock = 12_000L
        assertEquals(allTags, pending.applyTo(status()).notDone)
    }

    @Test
    fun `an untouched status is returned unchanged`() {
        val pending = PendingCompletions(now = { 10_000L })
        val s = status()
        assertEquals(s, pending.applyTo(s))
    }

    @Test
    fun `encode and decode round-trip including a null block`() {
        val m = mapOf(
            "-1t" to PendingCompletions.Entry("巳", 123L),
            "سمش" to PendingCompletions.Entry(null, 456L),
        )
        assertEquals(m, PendingCompletions.decode(PendingCompletions.encode(m)))
        assertEquals(emptyMap<String, PendingCompletions.Entry>(), PendingCompletions.decode(""))
    }
}
