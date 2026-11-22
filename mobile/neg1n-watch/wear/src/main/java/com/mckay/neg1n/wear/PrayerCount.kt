package com.mckay.neg1n.wear

import java.time.Instant
import java.time.ZoneId

/** What the prayer complication shows between a tap and the phone's next
 * sync. Pure (no Android types) so wear/src/test covers it; PrayerTap.kt is
 * the Android half.
 *
 * A tap is sent to the phone and applied on Ix at most once (see
 * tools/watch/neg1n_status.py, "Prayer tap"). The watch cannot see whether
 * it landed, so it shows its own count until a sync stamped SETTLE_MS after
 * the tap arrives: that one is authoritative, also when it is lower (the tap
 * was lost, or the count was corrected with /s). A sync inside SETTLE_MS may
 * have read the sheet before the increment was written, so until then the
 * higher of the two is shown. */
object PrayerCount {
    const val SETTLE_MS = 90_000L
    /** A second tap this soon is a double-touch, not a second prayer. */
    const val DEBOUNCE_MS = 5_000L

    data class Optimistic(val count: Int, val tapAtMillis: Long)

    private fun sameDay(a: Long, b: Long, zone: ZoneId) =
        Instant.ofEpochMilli(a).atZone(zone).toLocalDate() == Instant.ofEpochMilli(b).atZone(zone).toLocalDate()

    /** The synced count if that sync is from today. The payload carries no
     * date: a count last synced yesterday is yesterday's, and today's is 0. */
    fun remoteToday(remote: Int?, remoteUpdatedAt: Long, now: Long, zone: ZoneId): Int? =
        if (remoteUpdatedAt > 0L && sameDay(remoteUpdatedAt, now, zone)) remote else null

    /** The tap count while it still stands: same day, and no sync has
     * arrived from more than SETTLE_MS after the tap. */
    fun live(opt: Optimistic?, remoteUpdatedAt: Long, now: Long, zone: ZoneId): Optimistic? =
        opt?.takeIf { sameDay(it.tapAtMillis, now, zone) && remoteUpdatedAt <= it.tapAtMillis + SETTLE_MS }

    /** The number on the complication. Without a standing tap this is the
     * synced value untouched, exactly as before taps existed. */
    fun shown(remote: Int?, remoteUpdatedAt: Long, opt: Optimistic?, now: Long, zone: ZoneId): Int? {
        val o = live(opt, remoteUpdatedAt, now, zone) ?: return remote
        return maxOf(remoteToday(remote, remoteUpdatedAt, now, zone) ?: 0, o.count)
    }

    /** The count to show after a tap delivered at `now`. */
    fun afterTap(remote: Int?, remoteUpdatedAt: Long, opt: Optimistic?, now: Long, zone: ZoneId): Optimistic {
        val base = maxOf(
            remoteToday(remote, remoteUpdatedAt, now, zone) ?: 0,
            live(opt, remoteUpdatedAt, now, zone)?.count ?: 0,
        )
        return Optimistic(base + 1, now)
    }

    fun debounced(lastTapAt: Long, now: Long): Boolean = now - lastTapAt in 0 until DEBOUNCE_MS
}
