package com.mckay.neg1n.wear

/** Must match the phone module's Neg1nConfig exactly — see that file's
 * comment for why this isn't a shared module. */
object Neg1nConfig {
    const val DATA_PATH = "/neg1n_status"

    const val KEY_BLOCK = "block"
    const val KEY_DONE = "done"
    const val KEY_NOT_DONE = "not_done"
    const val KEY_UPDATED_AT = "updated_at"

    const val PREFS_NAME = "neg1n_wear_prefs"

    /** The 5 rituals in a fixed, meaningful order (the order they occur
     * through a block: prayer, goal-set, inbox, time-log, task-pointing) —
     * bars are drawn left to right in this order, never re-sorted by
     * done/not-done state, so a given bar position always means the same
     * ritual. Tag -> (display label, bar color). Colors follow a fixed
     * categorical order (never reassigned/cycled) so a color always means
     * the same ritual across every render. */
    val RITUALS: List<Triple<String, String, Int>> = listOf(
        Triple("سمش", "prayer", 0xFF2A78D6.toInt()),   // blue
        Triple("-1g", "goal", 0xFFEB6834.toInt()),      // orange
        Triple("-1ibx", "inbox", 0xFF1BAF7A.toInt()),   // aqua
        Triple("-1t", "time", 0xFFEDA100.toInt()),      // yellow
        Triple("-1l", "tasks", 0xFFE87BA4.toInt()),     // magenta
    )

    /** Neutral "not done yet" color — same for every bar regardless of
     * which ritual, per the user's spec ("same color if I can't [distinguish
     * by done state]"). A muted gray reads clearly against both light and
     * dark watch faces without competing with the done colors. */
    const val NOT_DONE_COLOR = 0xFF5F5F5F.toInt()
}
