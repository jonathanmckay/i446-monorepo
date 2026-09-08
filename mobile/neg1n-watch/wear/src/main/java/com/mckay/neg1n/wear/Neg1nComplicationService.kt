package com.mckay.neg1n.wear

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

/** Status-only -1n complication: 5 bars, one per block ritual, colored when
 * done / neutral gray when not. No tap action is set anywhere in this file
 * (ComplicationData.Builder's tapAction is simply never called), so the
 * system falls back to "open the containing app" — MainActivity, a static
 * info screen with no further action, matching "status only" for now.
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
        )
        return buildData(type, preview)
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
                SmallImageComplicationData.Builder(image, contentDescription).build()
            }
            ComplicationType.SHORT_TEXT -> {
                // Degraded fallback for text-only slots — see manifest comment.
                // A short-text field can't carry per-bar color, only a count.
                val doneCount = Neg1nConfig.RITUALS.count { status.done.contains(it.first) }
                val text = PlainComplicationText.Builder("$doneCount/${Neg1nConfig.RITUALS.size}").build()
                ShortTextComplicationData.Builder(text, contentDescription).build()
            }
            else -> null
        }
    }
}
