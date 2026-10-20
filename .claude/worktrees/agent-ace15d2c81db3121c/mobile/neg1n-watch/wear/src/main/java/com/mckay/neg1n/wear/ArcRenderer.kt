package com.mckay.neg1n.wear

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF

/** Draws the day-分 quarter-circle progress gauge: a 90°-sweep arc track
 * (12 o'clock to 3 o'clock) with a colored progress arc on top, filled
 * proportionally to points/max. Same fixed-size-bitmap convention as
 * BarRenderer — square, centered content survives a circular/rounded-square
 * SMALL_IMAGE crop at any of the standard sizes. */
object ArcRenderer {

    private const val SIZE = 96 // px, system will scale as needed
    private const val START_ANGLE = -90f  // 12 o'clock, Android's 0deg = 3 o'clock
    private const val SWEEP_ANGLE = 90f   // quarter circle, clockwise to 3 o'clock

    private const val TRACK_COLOR = 0xFF3A3A3A.toInt()
    private const val PROGRESS_COLOR = 0xFF2A78D6.toInt() // matches -1n's "prayer" blue

    fun render(points: Int?, max: Int): Bitmap {
        val bitmap = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(Color.TRANSPARENT)

        val strokeWidth = SIZE * 0.16f
        val inset = strokeWidth / 2f + SIZE * 0.06f
        val oval = RectF(inset, inset, SIZE - inset, SIZE - inset)

        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            this.strokeWidth = strokeWidth
            strokeCap = Paint.Cap.ROUND
        }

        paint.color = TRACK_COLOR
        canvas.drawArc(oval, START_ANGLE, SWEEP_ANGLE, false, paint)

        if (points != null && max > 0) {
            val frac = (points.toFloat() / max.toFloat()).coerceIn(0f, 1f)
            if (frac > 0f) {
                paint.color = PROGRESS_COLOR
                canvas.drawArc(oval, START_ANGLE, SWEEP_ANGLE * frac, false, paint)
            }
        }

        return bitmap
    }

    /** Short human summary for the SHORT_TEXT fallback + content
     * description — e.g. "842分" or "no data" when nothing's synced yet. */
    fun summaryText(points: Int?): String = if (points == null) "no data" else "${points}分"
}
