package com.mckay.neg1n.wear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

/** Tap-to-increment on the prayer complication (2026-11-22): what the watch
 * shows between a tap and the phone's next settled sync. */
class PrayerCountTest {

    private val zone = ZoneId.of("America/Los_Angeles")

    private fun at(hour: Int, minute: Int, second: Int = 0, day: Int = 22) =
        ZonedDateTime.of(2026, 11, day, hour, minute, second, 0, zone).toInstant().toEpochMilli()

    @Test
    fun `without a tap the synced count is shown untouched`() {
        assertEquals(8, PrayerCount.shown(8, at(9, 0), null, at(9, 5), zone))
        assertNull(PrayerCount.shown(null, at(9, 0), null, at(9, 5), zone))
    }

    @Test
    fun `a tap adds one to today's synced count`() {
        val opt = PrayerCount.afterTap(8, at(9, 0), null, at(9, 5), zone)
        assertEquals(PrayerCount.Optimistic(9, at(9, 5)), opt)
        assertEquals(9, PrayerCount.shown(8, at(9, 0), opt, at(9, 5, 1), zone))
    }

    @Test
    fun `a first tap of the day starts from zero, not yesterday's count`() {
        // last sync was yesterday evening and said 5
        val opt = PrayerCount.afterTap(5, at(21, 0, day = 21), null, at(5, 30), zone)
        assertEquals(1, opt.count)
        assertEquals(1, PrayerCount.shown(5, at(21, 0, day = 21), opt, at(5, 31), zone))
    }

    @Test
    fun `a sync that raced the write does not pull the count back`() {
        val tap = at(9, 5)
        val opt = PrayerCount.Optimistic(9, tap)
        // sync-now 5 s after the tap still read 8
        assertEquals(9, PrayerCount.shown(8, tap + 5_000, opt, tap + 6_000, zone))
        // and one that already carries the increment shows it
        assertEquals(9, PrayerCount.shown(9, tap + 5_000, opt, tap + 6_000, zone))
    }

    @Test
    fun `a settled sync is authoritative, also when lower`() {
        val tap = at(9, 5)
        val opt = PrayerCount.Optimistic(9, tap)
        val later = tap + PrayerCount.SETTLE_MS + 1
        assertEquals(8, PrayerCount.shown(8, later, opt, later + 1000, zone))   // the tap was lost
        assertEquals(3, PrayerCount.shown(3, later, opt, later + 1000, zone))   // corrected with /s 3
        assertEquals(12, PrayerCount.shown(12, later, opt, later + 1000, zone))
    }

    @Test
    fun `a second tap counts on top of the first, and yesterday's tap does not carry over`() {
        val first = PrayerCount.afterTap(8, at(9, 0), null, at(9, 5), zone)
        val second = PrayerCount.afterTap(8, at(9, 0), first, at(9, 6), zone)
        assertEquals(10, second.count)
        val nextDay = PrayerCount.afterTap(8, at(9, 0), second, at(5, 0, day = 23), zone)
        assertEquals(1, nextDay.count)
    }

    @Test
    fun `a double touch within five seconds is one tap`() {
        assertTrue(PrayerCount.debounced(at(9, 5), at(9, 5, 4)))
        assertFalse(PrayerCount.debounced(at(9, 5), at(9, 5, 5)))
        assertFalse(PrayerCount.debounced(0L, at(9, 5)))
    }
}
