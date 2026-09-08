package com.mckay.neg1n.phone

import android.content.Context
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.tasks.await

/** Shared "push a status to the watch" step, used by StatusSyncWorker.
 *
 * Also carries the configured status endpoint along in the same DataMap
 * (KEY_ENDPOINT) — the watch's ritual-completion swipe list calls the
 * server directly over its own WiFi (see wear/RitualCompleter.kt) rather
 * than relaying the action through the phone, so it needs to know the
 * current endpoint without a separate config UI/sync path of its own. */
object DataLayerPush {
    suspend fun push(context: Context, status: Neg1nStatus, endpoint: String) {
        val dataClient = Wearable.getDataClient(context)
        val putRequest = PutDataMapRequest.create(Neg1nConfig.DATA_PATH).apply {
            dataMap.putString(Neg1nConfig.KEY_BLOCK, status.block ?: "")
            dataMap.putString(Neg1nConfig.KEY_DONE, status.done.joinToString(","))
            dataMap.putString(Neg1nConfig.KEY_NOT_DONE, status.notDone.joinToString(","))
            dataMap.putString(Neg1nConfig.KEY_ENDPOINT, endpoint)
            dataMap.putLong(Neg1nConfig.KEY_UPDATED_AT, System.currentTimeMillis())
        }.asPutDataRequest().setUrgent()
        dataClient.putDataItem(putRequest).await()
    }

    suspend fun pushDayPoints(context: Context, status: DayPointsStatus) {
        val dataClient = Wearable.getDataClient(context)
        val putRequest = PutDataMapRequest.create(Neg1nConfig.DATA_PATH_DAY_POINTS).apply {
            dataMap.putInt(Neg1nConfig.KEY_POINTS, status.points ?: -1)
            dataMap.putInt(Neg1nConfig.KEY_MAX, status.max)
            dataMap.putLong(Neg1nConfig.KEY_UPDATED_AT, System.currentTimeMillis())
        }.asPutDataRequest().setUrgent()
        dataClient.putDataItem(putRequest).await()
    }

    suspend fun pushHcb(context: Context, status: HcbStatus) {
        val dataClient = Wearable.getDataClient(context)
        val putRequest = PutDataMapRequest.create(Neg1nConfig.DATA_PATH_HCB).apply {
            dataMap.putInt(Neg1nConfig.KEY_CALORIES, status.calories ?: -1)
            dataMap.putInt(Neg1nConfig.KEY_HCBP_HCBC, status.hcbpHcbc ?: -1)
            dataMap.putInt(Neg1nConfig.KEY_GOAL, status.goal)
            dataMap.putLong(Neg1nConfig.KEY_UPDATED_AT, System.currentTimeMillis())
        }.asPutDataRequest().setUrgent()
        dataClient.putDataItem(putRequest).await()
    }

    suspend fun pushHcmp(context: Context, status: HcmpStatus) {
        val dataClient = Wearable.getDataClient(context)
        val putRequest = PutDataMapRequest.create(Neg1nConfig.DATA_PATH_HCMP).apply {
            dataMap.putInt(Neg1nConfig.KEY_PRAYERS, status.prayers ?: -1)
            dataMap.putInt(Neg1nConfig.KEY_HCMP_MINUTES, status.hcmpMinutes ?: -1)
            dataMap.putLong(Neg1nConfig.KEY_UPDATED_AT, System.currentTimeMillis())
        }.asPutDataRequest().setUrgent()
        dataClient.putDataItem(putRequest).await()
    }
}
