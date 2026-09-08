package com.mckay.neg1n.wear

import android.content.Context
import android.net.Uri
import android.util.Log
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withTimeoutOrNull

private const val TAG = "Neg1n"

/** Reads the latest -1n status directly from the Data Layer's local store,
 * rather than relying on StatusListenerService's DATA_CHANGED push having
 * been delivered.
 *
 * Bug found 2026-09-08 sideloading this: GMS's "ListenerService
 * verification" never registered our manifest-declared
 * StatusListenerService as an eligible DATA_CHANGED recipient for this
 * locally-sideloaded (non-Play-published) app — confirmed via `adb shell
 * dumpsys activity service .../WearableService`: the phone's pushed
 * DataItem genuinely arrives and is stored (visible in its "SetDataItem"
 * traffic stats), but the wear app's package never appears anywhere in that
 * dump as a registered listener, and the system logs
 * "Failed to deliver message to AppKey[...]" for our path every time. A
 * device reboot or Play Services update might eventually fix the
 * registration, but a synchronous pull here is strictly more robust for a
 * personal, sideloaded app: it doesn't depend on that registry at all.
 * Paired with UPDATE_PERIOD_SECONDS=900 in the manifest as the reliable
 * refresh trigger; StatusListenerService is left in place as a best-effort
 * fast path for whenever push delivery does work, but nothing here depends
 * on it. */
object DataLayerReader {

    suspend fun readLatest(context: Context): Neg1nStatus {
        val remote = readRemote(context)
        val local = StatusStore.load(context)
        // Whichever is more recent wins. This matters right after a
        // swipe-to-complete on the watch itself (RitualCompleter writes the
        // fresh result straight to StatusStore with a fresh timestamp): the
        // synced DataItem still reflects the phone's last push until its
        // own next periodic sync, which would otherwise silently overwrite
        // the just-completed ritual back to "not done" for up to 15 minutes.
        return if (remote != null && remote.updatedAtMillis >= local.updatedAtMillis) remote else local
    }

    private suspend fun readRemote(context: Context): Neg1nStatus? {
        try {
            val dataClient = Wearable.getDataClient(context)
            // Explicit "*" host = match this path regardless of which node
            // (the phone) originated it — Uri.Builder().scheme(...).path(...)
            // with NO authority is NOT the same query (it only matches items
            // with no authority at all, which no real synced item has); the
            // literal wildcard host is what GMS's cross-node read-back
            // pattern actually requires.
            val uri = Uri.parse("wear://*${Neg1nConfig.DATA_PATH}")
            val buffer = withTimeoutOrNull(3000) { dataClient.getDataItems(uri).await() }
            if (buffer != null) {
                try {
                    if (buffer.count > 0) {
                        val map = DataMapItem.fromDataItem(buffer[0]).dataMap
                        val block = map.getString(Neg1nConfig.KEY_BLOCK)?.ifBlank { null }
                        val done = (map.getString(Neg1nConfig.KEY_DONE) ?: "")
                            .split(",").filter { it.isNotBlank() }
                        val notDone = (map.getString(Neg1nConfig.KEY_NOT_DONE) ?: "")
                            .split(",").filter { it.isNotBlank() }
                        val updatedAt = map.getLong(Neg1nConfig.KEY_UPDATED_AT)
                        val endpoint = map.getString(Neg1nConfig.KEY_ENDPOINT)?.ifBlank { null }
                        val status = Neg1nStatus(block, done.toSet(), notDone, updatedAt, endpoint)
                        // Keep StatusStore warm as a fallback for whenever
                        // the Data Layer client itself is unavailable — but
                        // only if this remote value is actually newer, so it
                        // can't clobber a fresher local completion (see
                        // readLatest's comment).
                        val local = StatusStore.load(context)
                        if (updatedAt >= local.updatedAtMillis) {
                            StatusStore.save(context, block, done, notDone, updatedAt, endpoint)
                        }
                        return status
                    }
                } finally {
                    buffer.release()
                }
            } else {
                Log.e(TAG, "DataLayerReader: getDataItems timed out")
            }
        } catch (e: Exception) {
            Log.e(TAG, "DataLayerReader: live read failed", e)
        }
        return null
    }
}
