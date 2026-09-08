package com.mckay.neg1n.wear

/** In-memory, activity-lifetime overlay of ritual tags the user has just
 * swiped done on the watch, kept separate from whatever the Data Layer
 * currently reports.
 *
 * Bug found 2026-09-08: RitualListActivity.completeRitual() relays the
 * completion to the phone and waits RECONCILE_DELAY_MS (3s) before
 * reload()-ing from DataLayerReader.readLatest(). CompleteRitualWorker on
 * the phone does the real work (HTTP /complete + Data Layer push) inside a
 * plain, non-expedited WorkManager job dispatched from a background
 * WearableListenerService callback — under Doze/background execution
 * limits this can easily take longer than 3s to even start, let alone
 * finish and Bluetooth-sync the result back to the watch. When that race
 * is lost, reload() reads the still-stale Data Layer item (tag still in
 * not_done) and puts the just-swiped ritual right back in the list, even
 * though the server-side completion (confirmed via dtd/Todoist) already
 * succeeded.
 *
 * Once the user has swiped a tag done in this activity instance, it must
 * never reappear in that same instance's list again, regardless of how
 * stale the synced Data Layer item still looks — a fresh instance (next
 * time the list is opened) reads live data again, by which point the real
 * completion has long since landed. */
class PendingCompletions {
    private val tags = mutableSetOf<String>()

    fun markCompleted(tag: String) {
        tags.add(tag)
    }

    fun filter(notDone: List<String>): List<String> = notDone.filter { it !in tags }
}
