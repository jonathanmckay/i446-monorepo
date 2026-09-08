package com.mckay.neg1n.wear

import android.app.PendingIntent
import android.content.Intent
import android.graphics.drawable.Icon
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.RangedValueComplicationData
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.data.SmallImage
import androidx.wear.watchface.complications.data.SmallImageComplicationData
import androidx.wear.watchface.complications.data.SmallImageType
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import androidx.wear.watchface.complications.datasource.SuspendingComplicationDataSourceService

/** -1n complication: 5 bars, one per block ritual, colored when done /
 * neutral gray when not. Tapping opens RitualListActivity — a swipeable
 * list of this block's not-yet-done rituals, swipe left to complete.
 *
 * Extends the SUSPENDING variant (not the plain listener-callback one) so
 * DataLayerReader's Data Layer read can safely suspend rather than block —
 * onComplicationRequest is not guaranteed to run off the main thread
 * otherwise. */
class Neg1nComplicationService : SuspendingComplicationDataSourceService() {

    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData? {
        val status = DataLayerReader.readLatest(applicationContext)
        return buildData(request.complicationType, status)
    }

    override fun getPreviewData(type: ComplicationType): ComplicationData? {
        // Representative preview for the watch face's complication picker —
        // 3 of 5 done, so the picker shows the mixed-color case, not a blank
        // or all-neutral icon that would look broken.
        val preview = Neg1nStatus(
            block = "午",
            done = setOf("سمش", "-1g", "-1ibx"),
            notDone = listOf("-1t", "-1l"),
            updatedAtMillis = 0L,
            endpoint = null,
        )
        return buildData(type, preview)
    }

    private fun tapAction(): PendingIntent {
        val intent = Intent(applicationContext, RitualListActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
        return PendingIntent.getActivity(
            applicationContext, 0, intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }

    private fun buildData(type: ComplicationType, status: Neg1nStatus): ComplicationData? {
        val summary = BarRenderer.summaryText(status.done, status.notDone)
        val contentDescription = PlainComplicationText.Builder(
            if (status.block == null) "-1n: outside block hours" else "-1n block ${status.block}: $summary"
        ).build()

        return when (type) {
            ComplicationType.SMALL_IMAGE -> {
                val bitmap = BarRenderer.render(status.done)
                val icon = Icon.createWithBitmap(bitmap)
                val image = SmallImage.Builder(icon, SmallImageType.ICON).build()
                SmallImageComplicationData.Builder(image, contentDescription)
                    .setTapAction(tapAction())
                    .build()
            }
            ComplicationType.RANGED_VALUE -> {
                // The native-looking option for slots that don't render
                // SMALL_IMAGE at all (confirmed live 2026-09-08: the user's
                // slot only offered SHORT_TEXT). Rendered as a progress ring
                // by the watch face itself, in its own accent color — real
                // per-ritual bar colors aren't representable in a single
                // continuous value, but it's a visual fill (0-5), not just text.
                val doneCount = Neg1nConfig.RITUALS.count { status.done.contains(it.first) }
                val text = PlainComplicationText.Builder("$doneCount/${Neg1nConfig.RITUALS.size}").build()
                RangedValueComplicationData.Builder(
                    value = doneCount.toFloat(),
                    min = 0f,
                    max = Neg1nConfig.RITUALS.size.toFloat(),
                    contentDescription = contentDescription,
                ).setText(text)
                    .setTapAction(tapAction())
                    .build()
            }
            ComplicationType.SHORT_TEXT -> {
                // Degraded fallback for slots that support neither of the
                // above — see manifest comment. Plain text, no fill/color.
                val doneCount = Neg1nConfig.RITUALS.count { status.done.contains(it.first) }
                val text = PlainComplicationText.Builder("$doneCount/${Neg1nConfig.RITUALS.size}").build()
                ShortTextComplicationData.Builder(text, contentDescription)
                    .setTapAction(tapAction())
                    .build()
            }
            else -> null
        }
    }
}
