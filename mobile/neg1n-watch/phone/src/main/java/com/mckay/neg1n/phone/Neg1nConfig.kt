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
    const val KEY_ENDPOINT = "endpoint"

    const val PREFS_NAME = "neg1n_prefs"
    const val PREF_ENDPOINT = "endpoint_url"
    // Ix's Tailscale IP, not the "ix" MagicDNS hostname or a home-LAN IP —
    // this needs to resolve/route from wherever the phone actually is
    // (cellular data, someone else's WiFi, ...), not just at home or with
    // Tailscale MagicDNS specifically configured on this phone. The phone
    // needs Tailscale installed and logged in for this to resolve at all.
    const val DEFAULT_ENDPOINT = "http://100.114.46.109:5562/api/neg1n"

    const val SYNC_WORK_NAME = "neg1n_status_sync"

    // --- day-points, hcb, hcmp complications (added alongside -1n) ---
    // Sibling endpoints on the same neg1n_status.py Flask process/port —
    // derive from PREF_ENDPOINT's configured host rather than hardcoding it
    // a 2nd/3rd/4th time, so a changed endpoint (the phone's config screen)
    // moves all four together.
    fun dayPointsUrl(neg1nEndpoint: String) = neg1nEndpoint.replaceAfterLast('/', "day-points")
    fun hcbUrl(neg1nEndpoint: String) = neg1nEndpoint.replaceAfterLast('/', "hcb")
    fun hcmpUrl(neg1nEndpoint: String) = neg1nEndpoint.replaceAfterLast('/', "hcmp")

    const val DATA_PATH_DAY_POINTS = "/neg1n_day_points"
    const val DATA_PATH_HCB = "/neg1n_hcb"
    const val DATA_PATH_HCMP = "/neg1n_hcmp"

    const val KEY_POINTS = "points"
    const val KEY_MAX = "max"
    const val KEY_CALORIES = "calories"
    const val KEY_HCBP_HCBC = "hcbp_hcbc"
    const val KEY_GOAL = "goal"
    const val KEY_PRAYERS = "prayers"
    const val KEY_HCMP_MINUTES = "hcmp_minutes"
}
