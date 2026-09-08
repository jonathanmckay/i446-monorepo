package com.mckay.neg1n.wear

import android.content.Context

/** Local cache of the last status pushed by the phone. The complication
 * service reads this synchronously on every render request — it must never
 * block on a Data Layer round trip itself, since onComplicationRequest has a
 * short system-enforced time budget. StatusListenerService is the only
 * writer. */
data class Neg1nStatus(
    val block: String?,
    val done: Set<String>,
    val notDone: List<String>,
    val updatedAtMillis: Long,
)

object StatusStore {
    private const val KEY_BLOCK = "block"
    private const val KEY_DONE = "done"
    private const val KEY_NOT_DONE = "not_done"
    private const val KEY_UPDATED_AT = "updated_at"

    fun save(context: Context, block: String?, done: List<String>, notDone: List<String>, updatedAtMillis: Long) {
        context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE).edit()
            .putString(KEY_BLOCK, block ?: "")
            .putString(KEY_DONE, done.joinToString(","))
            .putString(KEY_NOT_DONE, notDone.joinToString(","))
            .putLong(KEY_UPDATED_AT, updatedAtMillis)
            .apply()
    }

    fun load(context: Context): Neg1nStatus {
        val prefs = context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
        val block = prefs.getString(KEY_BLOCK, "")?.ifBlank { null }
        val done = (prefs.getString(KEY_DONE, "") ?: "")
            .split(",").filter { it.isNotBlank() }.toSet()
        val notDone = (prefs.getString(KEY_NOT_DONE, "") ?: "")
            .split(",").filter { it.isNotBlank() }
        val updatedAt = prefs.getLong(KEY_UPDATED_AT, 0L)
        return Neg1nStatus(block, done, notDone, updatedAt)
    }
}
