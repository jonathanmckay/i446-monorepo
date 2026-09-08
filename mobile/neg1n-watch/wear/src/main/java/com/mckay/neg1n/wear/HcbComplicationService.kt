package com.mckay.neg1n.wear

import android.content.Context
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.RangedValueComplicationData
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import androidx.wear.watchface.complications.datasource.SuspendingComplicationDataSourceService

/** Calories eaten today (hcbi!U) + the combined hcbp+hcbc score against its
 * 131 goal (hcbi!X375+X378, a running Q2+Q3 total, not a daily figure).
 *
 * Uses RANGED_VALUE for the score-vs-goal half — a native ring the watch
 * face styles itself, purpose-built for exactly "value out of a range with
 * a goal" (per the rubber-duck-substitute pass on this feature: cheaper
 * than a 2nd custom bitmap renderer and looks better than one would). This
 * is why this service's rendering doesn't match DayPointsComplicationService's
 * ArcRenderer pattern — -1n's own service already varies its rendering by
 * requested type (custom bitmap for SMALL_IMAGE, plain text for SHORT_TEXT),
 * so offering a 3rd, native type here is consistent with that established
 * norm, not a break from it. */
class HcbComplicationService : SuspendingComplicationDataSourceService() {

    private object Cache {
        private const val KEY_CAL = "hcb_cached_calories"
        private const val KEY_SCORE = "hcb_cached_score"
        private const val KEY_GOAL = "hcb_cached_goal"

        fun save(context: Context, calories: Int?, score: Int?, goal: Int) {
            context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE).edit()
                .putInt(KEY_CAL, calories ?: Neg1nConfig.NO_VALUE)
                .putInt(KEY_SCORE, score ?: Neg1nConfig.NO_VALUE)
                .putInt(KEY_GOAL, goal)
                .apply()
        }

        fun load(context: Context): Triple<Int?, Int?, Int> {
            val prefs = context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
            val cal = prefs.getInt(KEY_CAL, Neg1nConfig.NO_VALUE).takeIf { it != Neg1nConfig.NO_VALUE }
            val score = prefs.getInt(KEY_SCORE, Neg1nConfig.NO_VALUE).takeIf { it != Neg1nConfig.NO_VALUE }
            val goal = prefs.getInt(KEY_GOAL, 131)
            return Triple(cal, score, goal)
        }
    }

    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData? {
        val remote = GenericComplicationData.readRemote(applicationContext, Neg1nConfig.DATA_PATH_HCB)
        val (calories, score, goal) = if (remote != null) {
            val cal = remote.getInt(Neg1nConfig.KEY_CALORIES, Neg1nConfig.NO_VALUE)
                .takeIf { it != Neg1nConfig.NO_VALUE }
            val sc = remote.getInt(Neg1nConfig.KEY_HCBP_HCBC, Neg1nConfig.NO_VALUE)
                .takeIf { it != Neg1nConfig.NO_VALUE }
            val g = remote.getInt(Neg1nConfig.KEY_GOAL, 131)
            Cache.save(applicationContext, cal, sc, g)
            Triple(cal, sc, g)
        } else {
            Cache.load(applicationContext)
        }
        return buildData(request.complicationType, calories, score, goal)
    }

    override fun getPreviewData(type: ComplicationType): ComplicationData? {
        return buildData(type, calories = 1530, score = 118, goal = 131)
    }

    private fun buildData(type: ComplicationType, calories: Int?, score: Int?, goal: Int): ComplicationData? {
        val calText = if (calories == null) "no data" else "${calories}kcal"
        val scoreText = if (score == null) "no data" else "$score/$goal"
        val contentDescription = PlainComplicationText.Builder("hcb: $calText, hcbp+hcbc $scoreText").build()

        return when (type) {
            ComplicationType.RANGED_VALUE -> {
                RangedValueComplicationData.Builder(
                    value = (score ?: 0).toFloat().coerceIn(0f, goal.toFloat()),
                    min = 0f,
                    max = goal.toFloat().coerceAtLeast(1f),
                    contentDescription = contentDescription,
                )
                    .setText(PlainComplicationText.Builder(calText).build())
                    .build()
            }
            ComplicationType.SHORT_TEXT -> {
                val text = PlainComplicationText.Builder("$calText · $scoreText").build()
                ShortTextComplicationData.Builder(text, contentDescription).build()
            }
            else -> null
        }
    }
}
