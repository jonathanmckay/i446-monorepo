package com.mckay.neg1n.phone

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.WorkerParameters

private const val TAG = "Neg1n"
const val KEY_RITUAL_TAG = "ritual_tag"

/** Completes one -1n ritual (relayed from the watch's swipe-to-complete list
 * via RitualCompleteListenerService) and pushes the resulting fresh status
 * straight back to the watch — one round trip, not fetch-then-push
 * separately, since the /complete endpoint already returns the recomputed
 * status. */
class CompleteRitualWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val tag = inputData.getString(KEY_RITUAL_TAG) ?: return Result.failure()
        val prefs = applicationContext.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
        val endpoint = prefs.getString(Neg1nConfig.PREF_ENDPOINT, Neg1nConfig.DEFAULT_ENDPOINT)!!
        Log.i(TAG, "CompleteRitualWorker: completing tag=$tag via $endpoint")

        val status = StatusFetcher(endpoint).complete(tag).getOrElse { e ->
            Log.e(TAG, "CompleteRitualWorker: complete failed for tag=$tag", e)
            return Result.retry()
        }
        Log.i(TAG, "CompleteRitualWorker: OK tag=$tag done=${status.done} not_done=${status.notDone}")

        return try {
            DataLayerPush.push(applicationContext, status)
            Result.success()
        } catch (e: Exception) {
            Log.e(TAG, "CompleteRitualWorker: Data Layer push failed after completing tag=$tag", e)
            // The ritual itself already succeeded server-side — don't retry
            // the whole completion, that would re-run --ritual on an
            // already-closed task. Let the next periodic StatusSyncWorker
            // pick up the fresh state instead.
            Result.success()
        }
    }
}

fun completeRitualInputData(tag: String): Data =
    Data.Builder().putString(KEY_RITUAL_TAG, tag).build()
