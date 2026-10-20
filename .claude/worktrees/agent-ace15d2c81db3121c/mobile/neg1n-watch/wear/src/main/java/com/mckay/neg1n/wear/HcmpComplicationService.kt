package com.mckay.neg1n.wear

import android.content.Context
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import androidx.wear.watchface.complications.datasource.SuspendingComplicationDataSourceService

/** Today's prayer count (0n!AP, صلاة) and combined hcmp minutes (0n!AQ+AR+AS
 * = o314 + 冥想 + 其他人). No stated goal for either number (unlike hcb's
 * 131), so no natural RANGED_VALUE/arc fit here — plain SHORT_TEXT, matching
 * -1n's own degraded-fallback text style ("N/5"). */
class HcmpComplicationService : SuspendingComplicationDataSourceService() {

    private object Cache {
        private const val KEY_PRAYERS = "hcmp_cached_prayers"
        private const val KEY_MINUTES = "hcmp_cached_minutes"

        fun save(context: Context, prayers: Int?, minutes: Int?) {
            context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE).edit()
                .putInt(KEY_PRAYERS, prayers ?: Neg1nConfig.NO_VALUE)
                .putInt(KEY_MINUTES, minutes ?: Neg1nConfig.NO_VALUE)
                .apply()
        }

        fun load(context: Context): Pair<Int?, Int?> {
            val prefs = context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
            val prayers = prefs.getInt(KEY_PRAYERS, Neg1nConfig.NO_VALUE).takeIf { it != Neg1nConfig.NO_VALUE }
            val minutes = prefs.getInt(KEY_MINUTES, Neg1nConfig.NO_VALUE).takeIf { it != Neg1nConfig.NO_VALUE }
            return prayers to minutes
        }
    }

    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData? {
        val remote = GenericComplicationData.readRemote(applicationContext, Neg1nConfig.DATA_PATH_HCMP)
        val (prayers, minutes) = if (remote != null) {
            val p = remote.getInt(Neg1nConfig.KEY_PRAYERS, Neg1nConfig.NO_VALUE)
                .takeIf { it != Neg1nConfig.NO_VALUE }
            val m = remote.getInt(Neg1nConfig.KEY_HCMP_MINUTES, Neg1nConfig.NO_VALUE)
                .takeIf { it != Neg1nConfig.NO_VALUE }
            Cache.save(applicationContext, p, m)
            p to m
        } else {
            Cache.load(applicationContext)
        }
        return buildData(request.complicationType, prayers, minutes)
    }

    override fun getPreviewData(type: ComplicationType): ComplicationData? {
        return buildData(type, prayers = 3, minutes = 57)
    }

    private fun buildData(type: ComplicationType, prayers: Int?, minutes: Int?): ComplicationData? {
        val prayersText = prayers?.toString() ?: "-"
        val minutesText = minutes?.let { "${it}m" } ?: "-"
        val summary = "$prayersText صلاة · $minutesText"
        val contentDescription = PlainComplicationText.Builder(
            "صلاة today: $prayersText, hcmp minutes: $minutesText"
        ).build()

        return when (type) {
            ComplicationType.SHORT_TEXT -> {
                val text = PlainComplicationText.Builder(summary).build()
                ShortTextComplicationData.Builder(text, contentDescription).build()
            }
            else -> null
        }
    }
}
