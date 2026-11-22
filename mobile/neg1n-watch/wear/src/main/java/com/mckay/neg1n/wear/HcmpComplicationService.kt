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

    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData? {
        val remote = GenericComplicationData.readRemote(applicationContext, Neg1nConfig.DATA_PATH_HCMP)
        val synced = if (remote != null) {
            HcmpCache.Synced(
                prayers = remote.getInt(Neg1nConfig.KEY_PRAYERS, Neg1nConfig.NO_VALUE)
                    .takeIf { it != Neg1nConfig.NO_VALUE },
                minutes = remote.getInt(Neg1nConfig.KEY_HCMP_MINUTES, Neg1nConfig.NO_VALUE)
                    .takeIf { it != Neg1nConfig.NO_VALUE },
                updatedAtMillis = remote.getLong(Neg1nConfig.KEY_UPDATED_AT, 0L),
            ).also { HcmpCache.save(applicationContext, it) }
        } else {
            HcmpCache.load(applicationContext)
        }
        // A tap on this complication (PrayerTap.kt) stands until the phone's
        // next settled sync; without one this is the synced count untouched.
        val prayers = PrayerCount.shown(
            synced.prayers, synced.updatedAtMillis, PrayerTapStore.load(applicationContext),
            System.currentTimeMillis(), java.time.ZoneId.systemDefault(),
        )
        return buildData(request.complicationType, prayers, synced.minutes)
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
                ShortTextComplicationData.Builder(text, contentDescription)
                    .setTapAction(PrayerTapStore.tapAction(applicationContext))
                    .build()
            }
            else -> null
        }
    }
}

/** The last synced hcmp numbers, kept for when the live Data Layer read
 * misses. Also read by PrayerTapReceiver as the base a tap adds to. */
internal object HcmpCache {
    private const val KEY_PRAYERS = "hcmp_cached_prayers"
    private const val KEY_MINUTES = "hcmp_cached_minutes"
    private const val KEY_UPDATED_AT = "hcmp_cached_updated_at"

    data class Synced(val prayers: Int?, val minutes: Int?, val updatedAtMillis: Long)

    fun save(context: Context, s: Synced) {
        context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE).edit()
            .putInt(KEY_PRAYERS, s.prayers ?: Neg1nConfig.NO_VALUE)
            .putInt(KEY_MINUTES, s.minutes ?: Neg1nConfig.NO_VALUE)
            .putLong(KEY_UPDATED_AT, s.updatedAtMillis)
            .apply()
    }

    fun load(context: Context): Synced {
        val prefs = context.getSharedPreferences(Neg1nConfig.PREFS_NAME, Context.MODE_PRIVATE)
        return Synced(
            prayers = prefs.getInt(KEY_PRAYERS, Neg1nConfig.NO_VALUE).takeIf { it != Neg1nConfig.NO_VALUE },
            minutes = prefs.getInt(KEY_MINUTES, Neg1nConfig.NO_VALUE).takeIf { it != Neg1nConfig.NO_VALUE },
            updatedAtMillis = prefs.getLong(KEY_UPDATED_AT, 0L),
        )
    }
}
