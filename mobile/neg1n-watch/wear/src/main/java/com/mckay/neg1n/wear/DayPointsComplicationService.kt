package com.mckay.neg1n.wear

import android.content.Context
import android.graphics.drawable.Icon
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.data.SmallImage
import androidx.wear.watchface.complications.data.SmallImageComplicationData
import androidx.wear.watchface.complications.data.SmallImageType
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import androidx.wear.watchface.complications.datasource.SuspendingComplicationDataSourceService

/** Today's total 分 (0分!D, the grand-total column) as a quarter-circle
 * progress arc, scaled against a fixed 1440 reference (not a real per-day
 * ceiling — see ArcRenderer/the server route's own comments). Status only,
 * no tap action, same as Neg1nComplicationService. */
class DayPointsComplicationService : SuspendingComplicationDataSourceService() {

    private object Cache {
        private const val KEY_POINTS = "day_points_cached_points"
        private const val KEY_MAX = "day_points_cached_max"

        fun save(context: Context, points: Int?, max: Int) {
            context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE).edit()
                .putInt(KEY_POINTS, points ?: Neg1nConfig.NO_VALUE)
                .putInt(KEY_MAX, max)
                .apply()
        }

        fun load(context: Context): Pair<Int?, Int> {
            val prefs = context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
            val points = prefs.getInt(KEY_POINTS, Neg1nConfig.NO_VALUE)
                .takeIf { it != Neg1nConfig.NO_VALUE }
            val max = prefs.getInt(KEY_MAX, 1440)
            return points to max
        }
    }

    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData? {
        val remote = GenericComplicationData.readRemote(applicationContext, Neg1nConfig.DATA_PATH_DAY_POINTS)
        val (points, max) = if (remote != null) {
            val p = remote.getInt(Neg1nConfig.KEY_POINTS, Neg1nConfig.NO_VALUE)
                .takeIf { it != Neg1nConfig.NO_VALUE }
            val m = remote.getInt(Neg1nConfig.KEY_MAX, 1440)
            Cache.save(applicationContext, p, m)
            p to m
        } else {
            Cache.load(applicationContext)
        }
        return buildData(request.complicationType, points, max)
    }

    override fun getPreviewData(type: ComplicationType): ComplicationData? {
        return buildData(type, points = 940, max = 1440)
    }

    private fun buildData(type: ComplicationType, points: Int?, max: Int): ComplicationData? {
        val contentDescription = PlainComplicationText.Builder(
            "day points: ${ArcRenderer.summaryText(points)}"
        ).build()

        return when (type) {
            ComplicationType.SMALL_IMAGE -> {
                val bitmap = ArcRenderer.render(points, max)
                val icon = Icon.createWithBitmap(bitmap)
                val image = SmallImage.Builder(icon, SmallImageType.ICON).build()
                SmallImageComplicationData.Builder(image, contentDescription).build()
            }
            ComplicationType.SHORT_TEXT -> {
                val text = PlainComplicationText.Builder(ArcRenderer.summaryText(points)).build()
                ShortTextComplicationData.Builder(text, contentDescription).build()
            }
            else -> null
        }
    }
}
