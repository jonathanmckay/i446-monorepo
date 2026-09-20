package com.mckay.neg1n.wear

import android.content.Context

/** Overlay of ritual tags the user has swiped done on the watch, applied on
 * top of whatever the Data Layer currently reports, and persisted across
 * activity instances.
 *
 * Bug found 2026-09-08: RitualListActivity.completeRitual() relays the
 * completion to the phone and waits RECONCILE_DELAY_MS (3s) before
 * reload()-ing from DataLayerReader.readLatest(). CompleteRitualWorker on
 * the phone does the real work (HTTP /complete + Data Layer push) inside a
 * plain WorkManager job dispatched from a background WearableListenerService
 * callback — under Doze this can take longer than 3s, so reload() read the
 * still-stale item and put the just-swiped ritual straight back in the list.
 * The first fix was an in-memory, activity-lifetime set.
 *
 * Bug found 2026-09-20: that wasn't enough. Opening the list also sends
 * "/neg1n_sync_now"; the phone's resulting fetch (pre-completion) could be
 * pushed AFTER the completion's own push, reverting the tag in the Data
 * Layer. A reopened list (fresh activity, empty in-memory set) and the
 * complication bar then showed the ritual not-done again until the next
 * 15-min periodic sync — with the server-side completion long since done
 * (Ix's ritual log showed the same tag re-swiped seconds later as
 * "stamped False": already complete). So the overlay is now persisted in
 * SharedPreferences and applied inside DataLayerReader.readLatest(), which
 * both the list and the complication service read through.
 *
 * Entries are pruned (in memory; only markCompleted persists, so complication
 * renders never write) once the remote status confirms the tag done, when
 * the block changes (a swipe in 午 must not hide 未's ritual), or after
 * [ttlMillis] — the phone's periodic sync is 15 min, so a swipe whose
 * completion genuinely failed can't hide a ritual for more than ~one
 * cycle plus change. Callers should only mark a swipe whose relay to the
 * phone was actually delivered. */
class PendingCompletions(
    initial: Map<String, Entry> = emptyMap(),
    private val now: () -> Long = System::currentTimeMillis,
    private val ttlMillis: Long = DEFAULT_TTL_MILLIS,
    private val persist: (Map<String, Entry>) -> Unit = {},
) {
    data class Entry(val block: String?, val swipedAtMillis: Long)

    private val entries: MutableMap<String, Entry> = initial.toMutableMap()

    fun markCompleted(tag: String, block: String?) {
        entries[tag] = Entry(block, now())
        persist(entries)
    }

    /** [status] with every still-pending tag for its block moved from
     * notDone to done (done too, not just out of notDone: BarRenderer colors
     * bars from `done`). Prunes confirmed/expired/other-block entries first. */
    fun applyTo(status: Neg1nStatus): Neg1nStatus {
        prune(status)
        val pending = status.notDone.filter { it in entries }
        if (pending.isEmpty()) return status
        return status.copy(done = status.done + pending, notDone = status.notDone - pending.toSet())
    }

    /** Tags currently held pending (after pruning against [status]). */
    fun pendingFor(status: Neg1nStatus): Set<String> {
        prune(status)
        return entries.keys.toSet()
    }

    private fun prune(status: Neg1nStatus) {
        val t = now()
        entries.entries.removeAll { (tag, e) ->
            tag in status.done || e.block != status.block || t - e.swipedAtMillis > ttlMillis
        }
    }

    companion object {
        const val DEFAULT_TTL_MILLIS = 20L * 60 * 1000
        private const val PREF_KEY = "pending_completions"

        /** Shared, prefs-backed instance. Cheap to construct per read: one
         * string parse from an already-loaded SharedPreferences map. */
        fun fromPrefs(context: Context): PendingCompletions {
            val prefs = context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
            return PendingCompletions(
                initial = decode(prefs.getString(PREF_KEY, "") ?: ""),
                persist = { m -> prefs.edit().putString(PREF_KEY, encode(m)).apply() },
            )
        }

        /** `tag|block|swipedAt;...` — tags/blocks never contain `|` or `;`. */
        fun encode(m: Map<String, Entry>): String =
            m.entries.joinToString(";") { (tag, e) -> "$tag|${e.block ?: ""}|${e.swipedAtMillis}" }

        fun decode(s: String): Map<String, Entry> =
            s.split(";").filter { it.isNotBlank() }.mapNotNull { part ->
                val f = part.split("|")
                if (f.size != 3) return@mapNotNull null
                val ts = f[2].toLongOrNull() ?: return@mapNotNull null
                f[0] to Entry(f[1].ifBlank { null }, ts)
            }.toMap()
    }
}
