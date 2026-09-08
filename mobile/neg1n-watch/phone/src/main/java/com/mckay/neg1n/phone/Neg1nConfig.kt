package com.mckay.neg1n.phone

/** Shared constants between phone and wear sides of the Data Layer sync.
 * Mirrored by an identical object in the wear module (no shared module —
 * two tiny files are simpler than a third Gradle module for one app). */
object Neg1nConfig {
    /** Wear Data Layer path the phone writes to and the watch listens on. */
    const val DATA_PATH = "/neg1n_status"

    // DataMap keys — must match the wear module's Neg1nConfig exactly.
    const val KEY_BLOCK = "block"
    const val KEY_DONE = "done"       // comma-joined ritual tags
    const val KEY_NOT_DONE = "not_done"
    const val KEY_UPDATED_AT = "updated_at"

    const val PREFS_NAME = "neg1n_prefs"
    const val PREF_ENDPOINT = "endpoint_url"
    const val DEFAULT_ENDPOINT = "http://ix:5562/api/neg1n"

    const val SYNC_WORK_NAME = "neg1n_status_sync"
}
