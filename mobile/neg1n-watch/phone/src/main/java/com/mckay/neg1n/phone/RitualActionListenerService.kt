package com.mckay.neg1n.phone

import android.util.Log
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService

private const val TAG = "Neg1n"

/** Reacts to messages sent from the watch — see wear/PhoneMessenger.kt (the
 * transport: plain Bluetooth, works regardless of either device's own
 * network/location) and its two callers, RitualListActivity's swipe-to-
 * complete and its sync-on-open request.
 *
 * Manifest-registered like StatusListenerService, whose doc comment records
 * that the EQUIVALENT delivery in the other direction (phone->watch
 * DATA_CHANGED) was unreliable for this sideloaded app via GMS's
 * "ListenerService verification". This is the untested direction (watch->
 * phone MESSAGE_RECEIVED) — if it turns out equally flaky, the fix would be
 * an in-process MessageClient listener kept alive by a foreground service,
 * but that's real added complexity (a persistent notification) not worth
 * building speculatively before confirming this path actually needs it. */
class RitualActionListenerService : WearableListenerService() {

    override fun onMessageReceived(event: MessageEvent) {
        when (event.path) {
            "/neg1n_complete" -> {
                val tag = String(event.data, Charsets.UTF_8)
                Log.i(TAG, "RitualActionListenerService: complete request tag=$tag")
                val request = OneTimeWorkRequestBuilder<CompleteRitualWorker>()
                    .setInputData(completeRitualInputData(tag))
                    .build()
                // Unique per-tag: a rapid double-swipe (or a retried
                // message) on the same ritual must not fire --ritual twice,
                // but two DIFFERENT rituals close together must not cancel
                // each other.
                WorkManager.getInstance(applicationContext).enqueueUniqueWork(
                    "neg1n_complete_$tag", ExistingWorkPolicy.KEEP, request)
            }
            "/neg1n_sync_now" -> {
                Log.i(TAG, "RitualActionListenerService: sync-now request")
                SyncScheduler.syncNow(applicationContext)
            }
            else -> return
        }
    }
}
