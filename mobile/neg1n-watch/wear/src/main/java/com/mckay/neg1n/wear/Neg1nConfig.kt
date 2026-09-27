package com.mckay.neg1n.wear

/** Must match the phone module's Neg1nConfig exactly — see that file's
 * comment for why this isn't a shared module. */
object Neg1nConfig {
    const val DATA_PATH = "/neg1n_status"

    const val KEY_BLOCK = "block"
    const val KEY_DONE = "done"
    const val KEY_NOT_DONE = "not_done"
    const val KEY_UPDATED_AT = "updated_at"
    const val KEY_ENDPOINT = "endpoint"

    const val PREFS_NAME = "neg1n_wear_prefs"

    // --- day-points, hcb, hcmp complications (added alongside -1n) ---
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

    /** Sentinel the phone pushes for an Int field it has no value for yet
     * (DataMap ints can't be null) — see phone/DataLayerPush.kt. Treat as
     * "no data" everywhere on the wear side too. */
    const val NO_VALUE = -1

    /** The 5 rituals in a fixed, meaningful order (the order they occur
     * through a block: prayer, goal-set, inbox, time-log, task-pointing) —
     * bars are drawn left to right in this order, never re-sorted by
     * done/not-done state, so a given bar position always means the same
     * ritual.
     *
     * Colors are NOT an independent categorical palette — they're pulled
     * straight from the existing Neon/dtd domain color system
     * (RITUAL_DOMAIN + COLORS in tools/dtd/dtd.py, mirrored from
     * tools/did/dtd.sh), so a ritual's bar matches the same color it
     * already has everywhere else in this system: سمش→hcm, -1g/-1l→g245
     * (same domain, same color — the two ARE the same color elsewhere too,
     * not a coloring bug here), -1ibx→i9, -1t→n156. Tag -> (display label,
     * bar color). */
    // Display labels are the ritual cards' exact Todoist/dtd names (the
    // auto_marker + tag from config/block-rituals.json, e.g. "😈 -1g"), not
    // English paraphrases — the watch list and the dtd CLI must show the same
    // names (2026-09-27, per JM).
    val RITUALS: List<Triple<String, String, Int>> = listOf(
        Triple("سمش", "😈 سمش", 0xFFAA00FF.toInt()),   // hcm
        Triple("-1g", "😈 -1g", 0xFF00E676.toInt()),       // g245
        Triple("-1ibx", "😈 -1ibx", 0xFF2979FF.toInt()),   // i9
        Triple("-1t", "😈 -1t", 0xFF1249B4.toInt()),      // n156
        Triple("-1l", "😈 -1l", 0xFF00E676.toInt()),     // g245
    )

    /** Neutral "not done yet" color — same for every bar regardless of
     * which ritual, per the user's spec ("same color if I can't [distinguish
     * by done state]"). A muted gray reads clearly against both light and
     * dark watch faces without competing with the done colors. */
    const val NOT_DONE_COLOR = 0xFF5F5F5F.toInt()
}
