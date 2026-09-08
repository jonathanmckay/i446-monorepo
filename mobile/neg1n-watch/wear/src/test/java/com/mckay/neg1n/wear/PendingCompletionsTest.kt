package com.mckay.neg1n.wear

import org.junit.Assert.assertEquals
import org.junit.Test

/** Regression coverage for the 2026-09-08 bug: swiping a ritual done on the
 * watch could revert to "not done" a few seconds later because
 * RitualListActivity's reconcile reload() raced the phone's real
 * completion work and sometimes read stale Data Layer state. */
class PendingCompletionsTest {

    @Test
    fun `a completed tag is filtered out of a stale not_done list`() {
        val pending = PendingCompletions()
        pending.markCompleted("-1l")

        // Simulates reload() reading a Data Layer item that hasn't caught
        // up yet — the phone's push hadn't landed within the 3s reconcile
        // window, so the synced item still lists -1l as not done.
        val staleNotDone = listOf("سمش", "-1g", "-1ibx", "-1t", "-1l")

        assertEquals(listOf("سمش", "-1g", "-1ibx", "-1t"), pending.filter(staleNotDone))
    }

    @Test
    fun `an already-completed tag stays filtered even across repeated reloads`() {
        val pending = PendingCompletions()
        pending.markCompleted("-1l")

        // Two reload() calls in a row (e.g. the immediate "whatever's
        // cached" reload plus the delayed reconcile reload) must both stay
        // consistent — -1l can never reappear once swiped this session.
        val staleNotDone = listOf("-1l", "-1t")
        assertEquals(listOf("-1t"), pending.filter(staleNotDone))
        assertEquals(listOf("-1t"), pending.filter(staleNotDone))
    }

    @Test
    fun `an untouched not_done list is unaffected`() {
        val pending = PendingCompletions()
        val notDone = listOf("سمش", "-1g", "-1ibx", "-1t", "-1l")
        assertEquals(notDone, pending.filter(notDone))
    }
}
