package com.mckay.neg1n.wear

import android.content.ComponentName
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import com.google.android.gms.wearable.DataEvent
import com.google.android.gms.wearable.DataEventBuffer
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.WearableListenerService

/** Reacts to the phone pushing a fresh -1n status over the Data Layer:
 * caches it locally (StatusStore) and asks the system to re-invoke
 * Neg1nComplicationService.onComplicationRequest so any watch face
 * currently showing this complication redraws immediately, instead of
 * waiting for whatever refresh cadence the watch face itself polls on. */
class StatusListenerService : WearableListenerService() {

    override fun onDataChanged(dataEvents: DataEventBuffer) {
        var changed = false
        dataEvents.forEach { event ->
            if (event.type != DataEvent.TYPE_CHANGED) return@forEach
            if (event.dataItem.uri.path != Neg1nConfig.DATA_PATH) return@forEach
            val map = DataMapItem.fromDataItem(event.dataItem).dataMap
            val block = map.getString(Neg1nConfig.KEY_BLOCK)?.ifBlank { null }
            val done = (map.getString(Neg1nConfig.KEY_DONE) ?: "")
                .split(",").filter { it.isNotBlank() }
            val notDone = (map.getString(Neg1nConfig.KEY_NOT_DONE) ?: "")
                .split(",").filter { it.isNotBlank() }
            val updatedAt = map.getLong(Neg1nConfig.KEY_UPDATED_AT)
            StatusStore.save(applicationContext, block, done, notDone, updatedAt)
            changed = true
        }
        dataEvents.release()
        if (changed) {
            val requester = ComplicationDataSourceUpdateRequester.create(
                applicationContext,
                ComponentName(applicationContext, Neg1nComplicationService::class.java),
            )
            requester.requestUpdateAll()
        }
    }
}
