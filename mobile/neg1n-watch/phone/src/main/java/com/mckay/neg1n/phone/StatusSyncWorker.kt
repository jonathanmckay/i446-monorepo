package com.mckay.neg1n.phone

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters

private const val TAG = "Neg1n"

/** Fetches status for all 4 complications from the home server and pushes
 * each to the paired watch over its own Wear Data Layer path. The watch
 * never makes a network call of its own — see wear/StatusListenerService.kt,
 * which reacts to a DataItem changing and asks the system to re-render the
 * corresponding complication.
 *
 * One periodic WorkManager job for all 4 targets (not 4 separate periodic
 * workers) so there's a single battery/network wakeup per cycle, not 4.
 * -1n keeps its own dedicated retry-on-failure treatment (unchanged from
 * before this file grew 3 more targets) since it's also the fetch the
 * watch's tap-to-complete ritual list depends on being current; the 3 newer
 * ones (day-points/hcb/hcmp) are lower-stakes glance complications on a
 * 30min-cache-backed server anyway, so a single cycle's failure there just
 * logs and waits for the next periodic run rather than forcing a whole-job
 * retry.
 *
 * All outcomes log under tag "Neg1n" (`adb logcat -d | grep Neg1n`) —
 * WorkManager's own "Worker result RETRY" log line tells you IT retried,
 * never WHY; the exception is only visible here. */
class StatusSyncWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val prefs = applicationContext.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
        val endpoint = prefs.getString(Neg1nConfig.PREF_ENDPOINT, Neg1nConfig.DEFAULT_ENDPOINT)!!
        Log.i(TAG, "Worker: fetching $endpoint")

        val fetchStartedAt = System.currentTimeMillis()
        val status = StatusFetcher(endpoint).fetch().getOrElse { e ->
            Log.e(TAG, "Worker: -1n fetch failed (attempt $runAttemptCount): url=$endpoint", e)
            return retryOrWaitForNextPeriod()
        }
        Log.i(TAG, "Worker: -1n fetch OK block=${status.block} done=${status.done} not_done=${status.notDone}")

        val neg1nResult = try {
            if (completionOverlapsFetch(prefs, fetchStartedAt)) {
                // A ritual completion began within the guard window of this
                // fetch: the fetched -1n status may predate the server-side
                // stamp, and pushing it would revert the swipe on the watch.
                // CompleteRitualWorker pushes the authoritative status.
                Log.i(TAG, "Worker: -1n push skipped, completion in flight")
            } else {
                DataLayerPush.push(applicationContext, status, endpoint, fetchStartedAt)
                Log.i(TAG, "Worker: -1n pushed to Data Layer OK")
            }
            Result.success()
        } catch (e: Exception) {
            Log.e(TAG, "Worker: -1n Data Layer push failed (attempt $runAttemptCount)", e)
            retryOrWaitForNextPeriod()
        }

        syncBestEffort("day-points") {
            val s = DayPointsFetcher(Neg1nConfig.dayPointsUrl(endpoint)).fetch().getOrThrow()
            DataLayerPush.pushDayPoints(applicationContext, s)
        }
        syncBestEffort("hcb") {
            val s = HcbFetcher(Neg1nConfig.hcbUrl(endpoint)).fetch().getOrThrow()
            DataLayerPush.pushHcb(applicationContext, s)
        }
        syncBestEffort("hcmp") {
            val s = HcmpFetcher(Neg1nConfig.hcmpUrl(endpoint)).fetch().getOrThrow()
            DataLayerPush.pushHcmp(applicationContext, s)
        }

        return neg1nResult
    }

    /** Bounded retry. Unbounded Result.retry() with LINEAR 1-min backoff
     * looks harmless but WorkManager keeps growing the delay across
     * consecutive failures up to its 5-hour cap (WorkRequest.MAX_BACKOFF_MILLIS),
     * and a periodic job stuck in backoff does NOT also run on its 15-min
     * cadence. Bug 2026-09-19: Tailscale had been off on the phone for 9
     * days, every cycle failed, and once the phone was back on the tailnet
     * the next attempt was still hours away — every complication sat stale
     * with the server fully healthy. Two quick retries cover a transient
     * blip; after that, give up this cycle (Result.failure() on periodic
     * work just means "run again at the next period") so recovery is never
     * worse than one 15-min period after the network comes back. */
    private fun retryOrWaitForNextPeriod(): Result =
        if (runAttemptCount < 2) Result.retry() else Result.failure()

    private fun completionOverlapsFetch(prefs: android.content.SharedPreferences, fetchStartedAt: Long): Boolean {
        val startedAt = prefs.getLong(Neg1nConfig.PREF_COMPLETION_STARTED_AT, 0L)
        return startedAt > fetchStartedAt - Neg1nConfig.COMPLETION_GUARD_MILLIS
    }

    /** Fetch+push one of the newer, lower-stakes targets — logs and swallows
     * any failure so it never affects -1n's own success/retry result above,
     * and one target failing (e.g. the hcb route erroring) can't block the
     * other two from syncing this cycle. */
    private suspend inline fun syncBestEffort(name: String, block: () -> Unit) {
        try {
            block()
            Log.i(TAG, "Worker: $name synced OK")
        } catch (e: Exception) {
            Log.e(TAG, "Worker: $name sync failed, will retry next cycle", e)
        }
    }
}
