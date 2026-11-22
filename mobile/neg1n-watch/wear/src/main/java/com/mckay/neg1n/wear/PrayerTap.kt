package com.mckay.neg1n.wear

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.VibrationEffect
import android.os.Vibrator
import android.util.Log
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import java.time.ZoneId

private const val TAG = "Neg1n"
private const val ACTION_PRAYER_TAP = "com.mckay.neg1n.wear.PRAYER_TAP"
private const val REQUEST_PRAYER_TAP = 7310
private const val PREF_OPT_COUNT = "prayer_opt_count"
private const val PREF_OPT_AT = "prayer_opt_at"
private const val PREF_LAST_TAP_AT = "prayer_last_tap_at"
/** The ritual-completion path, reused: the phone relays any tag on it to
 * /api/neg1n/complete unchanged, so a prayer tap needs no phone rebuild. */
private const val COMPLETE_PATH = "/neg1n_complete"
private const val SYNC_NOW_PATH = "/neg1n_sync_now"
private const val PRAYER_TAG_PREFIX = "ص:"

/** The standing tap count (PrayerCount.Optimistic) in prefs. */
object PrayerTapStore {
    private fun prefs(context: Context) =
        context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)

    fun load(context: Context): PrayerCount.Optimistic? {
        val p = prefs(context)
        val at = p.getLong(PREF_OPT_AT, 0L)
        return if (at > 0L) PrayerCount.Optimistic(p.getInt(PREF_OPT_COUNT, 0), at) else null
    }

    fun save(context: Context, opt: PrayerCount.Optimistic) {
        prefs(context).edit().putInt(PREF_OPT_COUNT, opt.count).putLong(PREF_OPT_AT, opt.tapAtMillis).apply()
    }

    fun lastTapAt(context: Context) = prefs(context).getLong(PREF_LAST_TAP_AT, 0L)
    fun markTap(context: Context, now: Long) = prefs(context).edit().putLong(PREF_LAST_TAP_AT, now).apply()

    /** The prayer complication's tap action. A broadcast, not an activity:
     * nothing should open, the number just goes up. */
    fun tapAction(context: Context): PendingIntent = PendingIntent.getBroadcast(
        context, REQUEST_PRAYER_TAP,
        Intent(context, PrayerTapReceiver::class.java).setAction(ACTION_PRAYER_TAP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )
}

/** A tap on the prayer complication: +1 prayer today (the /s increment).
 * The tap's own timestamp is its nonce; the server applies each nonce at
 * most once, so nothing here retries. The on-watch count only moves when
 * the phone took the message: a short buzz for sent, a double buzz for not. */
class PrayerTapReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_PRAYER_TAP) return
        val app = context.applicationContext
        val now = System.currentTimeMillis()
        if (PrayerCount.debounced(PrayerTapStore.lastTapAt(app), now)) {
            Log.i(TAG, "PrayerTap: ignored, within ${PrayerCount.DEBOUNCE_MS} ms of the last tap")
            return
        }
        PrayerTapStore.markTap(app, now)
        // goAsync: about 10 s. Send (3 s) + settle (3.5 s) + sync-now (2 s).
        val pending = goAsync()
        CoroutineScope(Dispatchers.Default).launch {
            try {
                val sent = withTimeoutOrNull(3000) {
                    PhoneMessenger.send(app, COMPLETE_PATH, "$PRAYER_TAG_PREFIX$now")
                } == true
                if (sent) {
                    val remote = HcmpCache.load(app)
                    val next = PrayerCount.afterTap(
                        remote.prayers, remote.updatedAtMillis, PrayerTapStore.load(app), now, ZoneId.systemDefault())
                    PrayerTapStore.save(app, next)
                    Log.i(TAG, "PrayerTap: sent, showing ${next.count}")
                } else {
                    Log.e(TAG, "PrayerTap: not delivered to the phone, count unchanged")
                }
                buzz(app, sent)
                ComplicationDataSourceUpdateRequester
                    .create(app, ComponentName(app, HcmpComplicationService::class.java))
                    .requestUpdateAll()
                if (sent) {
                    // Not at once: the phone would fetch before Ix has written.
                    delay(3500)
                    withTimeoutOrNull(2000) { PhoneMessenger.send(app, SYNC_NOW_PATH) }
                }
            } catch (e: Exception) {
                Log.e(TAG, "PrayerTap: failed", e)
            } finally {
                pending.finish()
            }
        }
    }

    private fun buzz(context: Context, ok: Boolean) {
        try {
            val effect = if (ok) VibrationEffect.createOneShot(70, VibrationEffect.DEFAULT_AMPLITUDE)
            else VibrationEffect.createWaveform(longArrayOf(0, 200, 120, 200), -1)
            context.getSystemService(Vibrator::class.java)?.vibrate(effect)
        } catch (e: Exception) {
            Log.e(TAG, "PrayerTap: vibrate failed", e)
        }
    }
}
