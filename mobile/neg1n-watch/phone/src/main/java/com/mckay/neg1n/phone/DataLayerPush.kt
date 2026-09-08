package com.mckay.neg1n.phone

import android.content.Context
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.tasks.await

/** Shared "push a status to the watch" step, used by both the periodic
 * StatusSyncWorker and CompleteRitualWorker (which pushes the status the
 * backend already recomputed as part of completing a ritual, instead of
 * doing a second fetch). */
object DataLayerPush {
    suspend fun push(context: Context, status: Neg1nStatus) {
        val dataClient = Wearable.getDataClient(context)
        val putRequest = PutDataMapRequest.create(Neg1nConfig.DATA_PATH).apply {
            dataMap.putString(Neg1nConfig.KEY_BLOCK, status.block ?: "")
            dataMap.putString(Neg1nConfig.KEY_DONE, status.done.joinToString(","))
            dataMap.putString(Neg1nConfig.KEY_NOT_DONE, status.notDone.joinToString(","))
            dataMap.putLong(Neg1nConfig.KEY_UPDATED_AT, System.currentTimeMillis())
        }.asPutDataRequest().setUrgent()
        dataClient.putDataItem(putRequest).await()
    }
}
