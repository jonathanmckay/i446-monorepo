package com.mckay.neg1n.wear

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

private const val TAG = "Neg1n"
private const val ACTION_NUDGE = "com.mckay.neg1n.wear.NUDGE"
private const val REQUEST_ALARM = 7301
private const val REQUEST_OPEN_LIST = 7302
private const val NOTIFICATION_ID = 7301
private const val CHANNEL_ID = "neg1n_nudge"
private const val PREF_HANDLED_SLOT = "nudge_handled_slot"
private const val SYNC_NOW_PATH = "/neg1n_sync_now"
// Test nudge (`install-wear.sh test HH:MM`): its own alarm, notification id
// and pref, so the real nudge's handled-slot and cancel-on-done never see it.
private const val ACTION_NUDGE_TEST = "com.mckay.neg1n.wear.NUDGE_TEST"
private const val REQUEST_TEST_ALARM = 7304
private const val TEST_NOTIFICATION_ID = 7305
private const val PREF_TEST_AT = "nudge_test_at"
const val EXTRA_TEST_NUDGE_AT = "test_nudge_at"
// Always-test mode (`deploy-wear.sh always on [hours]`, 2026-11-22): until
// PREF_ALWAYS_UNTIL, every block buzzes at +30 even when the real nudge is
// silent (all done, stale, late). Its own notification id and slot pref, so
// the real nudge's handled-slot never sees it. Expires by itself: prefs
// survive `adb install -r`, a plain switch would stay on for good.
private const val PREF_ALWAYS_UNTIL = "nudge_always_until"
private const val PREF_ALWAYS_CMD_AT = "nudge_always_cmd_at"
private const val PREF_ALWAYS_SLOT = "nudge_always_slot"
private const val ALWAYS_NOTIFICATION_ID = 7306
const val EXTRA_ALWAYS_UNTIL = "nudge_always_until"
const val EXTRA_ALWAYS_CMD_AT = "nudge_always_cmd_at"

/** Arms the one alarm that drives the nudge: it always points at the next
 * pre-sync (+25 min) or nudge (+30 min) instant, and every fire re-arms it.
 * Idempotent, so it is also called from the complication refresh and both
 * activities: an alarm lost to a reboot, an app update or a force-stop is
 * back within one complication period. */
object NudgeScheduler {
    fun arm(context: Context, nowMillis: Long = System.currentTimeMillis()) {
        val at = NudgeLogic.nextWake(nowMillis)
        val alarms = context.getSystemService(AlarmManager::class.java) ?: return
        val pi = PendingIntent.getBroadcast(
            context, REQUEST_ALARM,
            Intent(context, NudgeReceiver::class.java).setAction(ACTION_NUDGE),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        // canScheduleExactAlarms() is API 31; below that exact alarms need no
        // permission. Inexact is the fallback, not a failure: the 15-minute
        // nudge window absorbs the delay.
        val exactAllowed = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarms.canScheduleExactAlarms()
        try {
            if (exactAllowed) alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
            else alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        } catch (e: SecurityException) {
            Log.e(TAG, "NudgeScheduler: exact alarm refused, falling back to inexact", e)
            alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        }
    }

    /** Schedule a test notification at `atMillis` through the same kind of
     * exact alarm and the same channel as the real nudge. It proves alarm →
     * notification → vibration; it skips the phone sync and the decision.
     * Each timestamp is honoured once: the launch intent that carries it is
     * redelivered when the activity is recreated, and must not fire again.
     * Returns false when nothing was scheduled. */
    fun armTest(context: Context, atMillis: Long, nowMillis: Long = System.currentTimeMillis()): Boolean {
        val prefs = context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
        if (atMillis <= nowMillis || prefs.getLong(PREF_TEST_AT, 0L) == atMillis) return false
        val alarms = context.getSystemService(AlarmManager::class.java) ?: return false
        val pi = PendingIntent.getBroadcast(
            context, REQUEST_TEST_ALARM,
            Intent(context, NudgeReceiver::class.java).setAction(ACTION_NUDGE_TEST),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val exactAllowed = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarms.canScheduleExactAlarms()
        try {
            if (exactAllowed) alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMillis, pi)
            else alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMillis, pi)
        } catch (e: SecurityException) {
            Log.e(TAG, "NudgeScheduler: exact test alarm refused, falling back to inexact", e)
            alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMillis, pi)
        }
        prefs.edit().putLong(PREF_TEST_AT, atMillis).apply()
        Log.i(TAG, "Nudge: test armed for $atMillis (exact=$exactAllowed)")
        return true
    }
}

/** The alarm target, and the re-arm hook for boot and app update. */
class NudgeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        if (intent.action == ACTION_NUDGE_TEST) {
            NudgeCheck.postTest(app)
            return
        }
        if (intent.action != ACTION_NUDGE) {
            NudgeScheduler.arm(app)
            return
        }
        // Alarm broadcasts are foreground broadcasts: 10 s before an ANR.
        // onAlarm budgets 6.5 s for the phone round trip; 9 s is the backstop.
        val pending = goAsync()
        CoroutineScope(Dispatchers.Default).launch {
            try {
                withTimeoutOrNull(9000) { NudgeCheck.onAlarm(app) }
            } catch (e: Exception) {
                Log.e(TAG, "NudgeReceiver: check failed", e)
            } finally {
                pending.finish()
            }
        }
    }
}

object NudgeCheck {

    /** One alarm fire. At +25 it only asks the phone to sync, so the status
     * is fresh by +30. At +30 it asks once more, waits briefly for something
     * newer than what it already had, then decides on whatever it has. */
    suspend fun onAlarm(context: Context) {
        NudgeScheduler.arm(context) // first: a timeout below must not cost the next alarm
        val now = System.currentTimeMillis()
        val slot = NudgeLogic.slotAt(now) ?: return
        if (now < slot.nudgeAt) {
            val sent = withTimeoutOrNull(3000) { PhoneMessenger.send(context, SYNC_NOW_PATH) }
            Log.i(TAG, "Nudge: pre-sync for ${slot.block} sent=$sent")
            return
        }
        if (handled(context, slot)) return

        val before = StatusStore.load(context).updatedAtMillis
        var latest: Neg1nStatus? = null
        withTimeoutOrNull(6500) {
            PhoneMessenger.send(context, SYNC_NOW_PATH)
            repeat(4) {
                val s = DataLayerReader.readLatest(context)
                latest = s
                // Compared with the watch's own previous value, never with a
                // watch-clock time: updated_at is stamped by the phone's clock.
                if (s.updatedAtMillis > before) return@withTimeoutOrNull
                delay(1200)
            }
        }
        val status = latest
            ?: PendingCompletions.fromPrefs(context).applyTo(StatusStore.load(context))
        onStatus(context, status, fromAlarm = true)
    }

    /** Decide and act on a status. Called from the alarm, and from every -1n
     * complication refresh with the status it just read: that second caller
     * is what delivers a nudge whose alarm ran late or found only stale data,
     * and what clears the notification once the block is finished. */
    fun onStatus(
        context: Context, status: Neg1nStatus, nowMillis: Long = System.currentTimeMillis(),
        fromAlarm: Boolean = false,
    ) {
        val slot = NudgeLogic.slotAt(nowMillis)
        if (slot != null && status.block == slot.block && NudgeLogic.missing(status).isEmpty()) {
            context.getSystemService(NotificationManager::class.java)?.cancel(NOTIFICATION_ID)
        }
        when (val d = NudgeLogic.decide(nowMillis, status)) {
            is NudgeLogic.Decision.Nudge -> {
                if (slot == null || handled(context, slot)) return
                markHandled(context, slot)
                post(context, slot, d.missing)
            }
            NudgeLogic.Decision.AllDone -> slot?.let { markHandled(context, it) }
            NudgeLogic.Decision.Stale ->
                Log.i(TAG, "Nudge: status stale (block=${status.block} updatedAt=${status.updatedAtMillis}), holding")
            NudgeLogic.Decision.NotDue -> Unit
        }
        postAlwaysTest(context, slot, NudgeLogic.decide(nowMillis, status), nowMillis, fromAlarm)
    }

    /** Honour an `always on/off` command once: the launch intent carrying it
     * is redelivered when the activity is recreated and must not undo a later
     * command. `until` 0 turns the mode off. */
    fun setAlways(context: Context, until: Long, cmdAt: Long) {
        val p = prefs(context)
        if (cmdAt <= p.getLong(PREF_ALWAYS_CMD_AT, 0L)) return
        p.edit().putLong(PREF_ALWAYS_CMD_AT, cmdAt).putLong(PREF_ALWAYS_UNTIL, until).apply()
        Log.i(TAG, "Nudge: always-test until=$until (cmd=$cmdAt)")
    }

    /** Always-test mode: buzz once per block at +30 when the real nudge has
     * nothing to say. "status stale" waits for the alarm's own sync (or a
     * minute), so a complication refresh at +30:00 cannot label a block stale
     * seconds before fresh data produces the real nudge. */
    private fun postAlwaysTest(
        context: Context, slot: NudgeLogic.Slot?, d: NudgeLogic.Decision, nowMillis: Long, fromAlarm: Boolean,
    ) {
        if (slot == null || nowMillis >= prefs(context).getLong(PREF_ALWAYS_UNTIL, 0L)) return
        val note = NudgeLogic.testNote(d, nowMillis, slot) ?: return
        if (d is NudgeLogic.Decision.Stale && !fromAlarm && nowMillis < slot.nudgeAt + 60_000L) return
        synchronized(this) { // the alarm coroutine and a complication refresh can race here
            if (prefs(context).getLong(PREF_ALWAYS_SLOT, 0L) == slot.startMillis) return
            prefs(context).edit().putLong(PREF_ALWAYS_SLOT, slot.startMillis).apply()
        }
        val manager = channelReady(context, "${slot.block} always-test") ?: return
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_nudge)
            .setContentTitle("-1n ${slot.block} · test at 30m")
            .setContentText(note)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setTimeoutAfter(15 * 60_000L)
            .build()
        manager.notify(ALWAYS_NOTIFICATION_ID, notification)
        Log.i(TAG, "Nudge: always-test posted for ${slot.block} ($note)")
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)

    private fun handled(context: Context, slot: NudgeLogic.Slot) =
        prefs(context).getLong(PREF_HANDLED_SLOT, 0L) == slot.startMillis

    private fun markHandled(context: Context, slot: NudgeLogic.Slot) {
        prefs(context).edit().putLong(PREF_HANDLED_SLOT, slot.startMillis).apply()
    }

    /** The notification manager with the nudge channel in place, or null
     * (logged) when notifications are not allowed. Shared by the real nudge
     * and the test, so the test exercises the same permission and channel. */
    private fun channelReady(context: Context, what: String): NotificationManager? {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            Log.e(TAG, "Nudge: POST_NOTIFICATIONS not granted, $what dropped (open the app once to allow)")
            return null
        }
        val manager = context.getSystemService(NotificationManager::class.java) ?: return null
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "-1n 30-minute nudge", NotificationManager.IMPORTANCE_HIGH).apply {
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 250, 150, 250)
            }
        )
        return manager
    }

    /** The test alarm's target (NudgeScheduler.armTest). */
    fun postTest(context: Context) {
        val manager = channelReady(context, "test nudge") ?: return
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_nudge)
            .setContentTitle("-1n test nudge")
            .setContentText("alarm, channel and buzz work")
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setTimeoutAfter(15 * 60_000L)
            .build()
        manager.notify(TEST_NOTIFICATION_ID, notification)
        Log.i(TAG, "Nudge: test posted")
    }

    private fun post(context: Context, slot: NudgeLogic.Slot, missing: List<String>) {
        val manager = channelReady(context, "${slot.block} nudge") ?: return
        val labelOf = Neg1nConfig.RITUALS.associate { (tag, label, _) -> tag to label }
        val open = PendingIntent.getActivity(
            context, REQUEST_OPEN_LIST,
            Intent(context, RitualListActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val now = System.currentTimeMillis()
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_nudge)
            .setContentTitle("-1n ${slot.block} · ${missing.size} left at 30m")
            .setContentText(missing.joinToString(" · ") { labelOf[it] ?: it })
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setTimeoutAfter((slot.endMillis - now).coerceAtLeast(60_000L))
            .build()
        manager.notify(NOTIFICATION_ID, notification)
        Log.i(TAG, "Nudge: posted for ${slot.block}, missing=$missing")
    }
}

/** POST_NOTIFICATIONS is a runtime permission from API 33; both activities
 * ask on open until it is granted (`adb shell pm grant com.mckay.neg1n
 * android.permission.POST_NOTIFICATIONS` does the same at install). */
fun android.app.Activity.requestNudgePermissionIfNeeded() {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) {
        requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 7303)
    }
}
