package com.mckay.neg1n.phone

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.google.android.gms.wearable.DataClient
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.tasks.await

/** Fetches -1n status from the home server and pushes it to the paired
 * watch over the Wear Data Layer. The watch never makes a network call of
 * its own — see wear/StatusListenerService.kt, which reacts to this DataItem
 * changing and asks the system to re-render the complication. */
class StatusSyncWorker(appContext: Context, params: WorkerParameters) :
    CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val prefs = applicationContext.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
        val endpoint = prefs.getString(Neg1nConfig.PREF_ENDPOINT, Neg1nConfig.DEFAULT_ENDPOINT)!!

        val status = StatusFetcher(endpoint).fetch().getOrElse {
            // Transient failure (phone offline, home server unreachable, ...) —
            // retry later rather than pushing stale/wrong data to the watch.
            return Result.retry()
        }

        val dataClient: DataClient = Wearable.getDataClient(applicationContext)
        val putRequest = PutDataMapRequest.create(Neg1nConfig.DATA_PATH).apply {
            dataMap.putString(Neg1nConfig.KEY_BLOCK, status.block ?: "")
            dataMap.putString(Neg1nConfig.KEY_DONE, status.done.joinToString(","))
            dataMap.putString(Neg1nConfig.KEY_NOT_DONE, status.notDone.joinToString(","))
            dataMap.putLong(Neg1nConfig.KEY_UPDATED_AT, System.currentTimeMillis())
        }.asPutDataRequest().setUrgent()

        return try {
            dataClient.putDataItem(putRequest).await()
            Result.success()
        } catch (e: Exception) {
            Result.retry()
        }
    }
}
