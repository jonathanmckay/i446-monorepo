package com.mckay.neg1n.wear

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF

/** Draws the 5-bar -1n status icon: one bar per ritual, in a fixed left-to-
 * right order, colored per Neg1nConfig.RITUALS when done, a shared neutral
 * gray when not. Rendered at a fixed size and let the system scale it into
 * whatever bounds the host watch face gives a SMALL_IMAGE complication
 * (typically cropped to a circle or rounded square) — square, centered
 * content survives that crop safely at any of the standard sizes. */
object BarRenderer {

    private const val SIZE = 96 // px, system will scale as needed

    fun render(done: Set<String>): Bitmap {
        val bitmap = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(Color.TRANSPARENT)

        val rituals = Neg1nConfig.RITUALS
        val count = rituals.size
        val barWidthFrac = 0.55f      // of each bar's own slot width
        val cornerRadius = SIZE * 0.05f
        val verticalInset = SIZE * 0.14f  // keep bars off the very top/bottom
        // edges, safe under a circular crop.
        val slotWidth = SIZE.toFloat() / count

        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        val top = verticalInset
        val bottom = SIZE - verticalInset

        rituals.forEachIndexed { i, (tag, _, color) ->
            paint.color = if (done.contains(tag)) color else Neg1nConfig.NOT_DONE_COLOR
            val slotCenter = slotWidth * (i + 0.5f)
            val barWidth = slotWidth * barWidthFrac
            val left = slotCenter - barWidth / 2f
            val right = slotCenter + barWidth / 2f
            canvas.drawRoundRect(RectF(left, top, right, bottom), cornerRadius, cornerRadius, paint)
        }

        return bitmap
    }

    /** Short human summary for the SHORT_TEXT fallback + accessibility
     * content description — e.g. "3/5 · goal, inbox left". */
    fun summaryText(done: Set<String>, notDone: List<String>): String {
        val labelOf = Neg1nConfig.RITUALS.associate { (tag, label, _) -> tag to label }
        val doneCount = Neg1nConfig.RITUALS.count { done.contains(it.first) }
        val total = Neg1nConfig.RITUALS.size
        if (notDone.isEmpty()) return "$doneCount/$total · all done"
        val remaining = notDone.mapNotNull { labelOf[it] }.joinToString(", ")
        return "$doneCount/$total · $remaining left"
    }
}
