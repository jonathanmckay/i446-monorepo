package com.mckay.neg1n.wear

import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime

/** When the 30-minute -1n nudge is due and whether it should buzz. Pure
 * (no Android types) so wear/src/test covers it; NudgeCheck is the Android
 * half that sends, reads and notifies.
 *
 * A block is nudged once, in the window [start+30min, start+45min): the
 * alarm normally fires at +30, and the 15-minute window lets a late alarm or
 * the complication's own refresh still deliver it (see NudgeCheck.onStatus).
 * Past the window the moment has gone and nothing fires. */
object NudgeLogic {
    const val PRESYNC_AFTER_MIN = 25L
    const val NUDGE_AFTER_MIN = 30L
    const val WINDOW_MIN = 15L
    /** A status older than this says nothing about now: the phone syncs every
     * 15 minutes and is asked again at +25, so anything older means the phone
     * is off the tailnet or out of range. */
    const val FRESH_WITHIN_MIN = 20L
    private const val MIN = 60_000L

    data class Slot(val block: String, val startMillis: Long) {
        val presyncAt: Long get() = startMillis + PRESYNC_AFTER_MIN * MIN
        val nudgeAt: Long get() = startMillis + NUDGE_AFTER_MIN * MIN
        val windowEnd: Long get() = nudgeAt + WINDOW_MIN * MIN
        val endMillis: Long get() = startMillis + Neg1nConfig.BLOCK_HOURS_LONG * 60 * MIN
    }

    sealed class Decision {
        /** Outside a nudge window (or outside block hours). */
        object NotDue : Decision()
        /** In the window, but the status is for another block or too old to
         * trust. Silent, and the slot stays open so fresher data arriving
         * inside the window can still fire it. */
        object Stale : Decision()
        object AllDone : Decision()
        data class Nudge(val block: String, val missing: List<String>) : Decision()
    }

    /** The block containing `nowMillis`, or null outside 04:00-22:00. */
    fun slotAt(nowMillis: Long, zone: ZoneId = Neg1nConfig.ZONE): Slot? {
        val now = ZonedDateTime.ofInstant(Instant.ofEpochMilli(nowMillis), zone)
        val (startHour, glyph) = Neg1nConfig.BLOCK_HOURS.lastOrNull { it.first <= now.hour } ?: return null
        if (now.hour >= startHour + Neg1nConfig.BLOCK_HOURS_LONG) return null
        val start = now.toLocalDate().atTime(startHour, 0).atZone(zone)
        return Slot(glyph, start.toInstant().toEpochMilli())
    }

    /** The next instant the alarm should fire: the earliest pre-sync (+25) or
     * nudge (+30) strictly after `nowMillis`, today or tomorrow. */
    fun nextWake(nowMillis: Long, zone: ZoneId = Neg1nConfig.ZONE): Long {
        val today = ZonedDateTime.ofInstant(Instant.ofEpochMilli(nowMillis), zone).toLocalDate()
        return (0L..1L).asSequence()
            .flatMap { day ->
                Neg1nConfig.BLOCK_HOURS.asSequence().map { (hour, glyph) ->
                    Slot(glyph, today.plusDays(day).atTime(hour, 0).atZone(zone).toInstant().toEpochMilli())
                }
            }
            .flatMap { sequenceOf(it.presyncAt, it.nudgeAt) }
            .first { it > nowMillis }
    }

    /** Rituals of the status's block not yet done, in the fixed bar order. */
    fun missing(status: Neg1nStatus): List<String> =
        Neg1nConfig.RITUALS.map { it.first }.filter { it !in status.done }

    fun decide(nowMillis: Long, status: Neg1nStatus, zone: ZoneId = Neg1nConfig.ZONE): Decision {
        val slot = slotAt(nowMillis, zone) ?: return Decision.NotDue
        if (nowMillis < slot.nudgeAt || nowMillis >= slot.windowEnd) return Decision.NotDue
        if (status.block != slot.block) return Decision.Stale
        if (status.updatedAtMillis < nowMillis - FRESH_WITHIN_MIN * MIN) return Decision.Stale
        val left = missing(status)
        return if (left.isEmpty()) Decision.AllDone else Decision.Nudge(slot.block, left)
    }
}
