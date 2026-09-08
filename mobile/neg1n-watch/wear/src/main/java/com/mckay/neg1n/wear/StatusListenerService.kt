package com.mckay.neg1n.wear

import android.content.ComponentName
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import com.google.android.gms.wearable.DataEvent
import com.google.android.gms.wearable.DataEventBuffer
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.WearableListenerService

/** Reacts to the phone pushing a fresh status over the Data Layer: caches
 * -1n's payload locally (StatusStore) and asks the system to re-invoke
 * each affected complication service's onComplicationRequest so any watch
 * face currently showing it redraws immediately, instead of waiting for
 * whatever refresh cadence the watch face itself polls on.
 *
 * Covers all 4 paths, not just -1n's (bug found 2026-09-08: this originally
 * only matched DATA_PATH and only requested an update for
 * Neg1nComplicationService, so day-points/hcb/hcmp pushes landed in the
 * Data Layer fine — GenericComplicationData.readRemote could see them —
 * but nothing ever told the system to re-render those 3 complications, so
 * they stayed on whatever stale/no-data state they last rendered until an
 * unrelated system refresh happened to hit them). */
class StatusListenerService : WearableListenerService() {

    override fun onDataChanged(dataEvents: DataEventBuffer) {
        var neg1nChanged = false
        val otherPathsChanged = mutableSetOf<String>()
        dataEvents.forEach { event ->
            if (event.type != DataEvent.TYPE_CHANGED) return@forEach
            when (event.dataItem.uri.path) {
                Neg1nConfig.DATA_PATH -> {
                    val map = DataMapItem.fromDataItem(event.dataItem).dataMap
                    val block = map.getString(Neg1nConfig.KEY_BLOCK)?.ifBlank { null }
                    val done = (map.getString(Neg1nConfig.KEY_DONE) ?: "")
                        .split(",").filter { it.isNotBlank() }
                    val notDone = (map.getString(Neg1nConfig.KEY_NOT_DONE) ?: "")
                        .split(",").filter { it.isNotBlank() }
                    val updatedAt = map.getLong(Neg1nConfig.KEY_UPDATED_AT)
                    val endpoint = map.getString(Neg1nConfig.KEY_ENDPOINT)?.ifBlank { null }
                    StatusStore.save(applicationContext, block, done, notDone, updatedAt, endpoint)
                    neg1nChanged = true
                }
                Neg1nConfig.DATA_PATH_DAY_POINTS,
                Neg1nConfig.DATA_PATH_HCB,
                Neg1nConfig.DATA_PATH_HCMP -> otherPathsChanged.add(event.dataItem.uri.path!!)
            }
        }
        dataEvents.release()
        if (neg1nChanged) {
            requestUpdate(Neg1nComplicationService::class.java)
        }
        if (Neg1nConfig.DATA_PATH_DAY_POINTS in otherPathsChanged) {
            requestUpdate(DayPointsComplicationService::class.java)
        }
        if (Neg1nConfig.DATA_PATH_HCB in otherPathsChanged) {
            requestUpdate(HcbComplicationService::class.java)
        }
        if (Neg1nConfig.DATA_PATH_HCMP in otherPathsChanged) {
            requestUpdate(HcmpComplicationService::class.java)
        }
    }

    private fun requestUpdate(service: Class<*>) {
        ComplicationDataSourceUpdateRequester.create(
            applicationContext,
            ComponentName(applicationContext, service),
        ).requestUpdateAll()
    }
}
