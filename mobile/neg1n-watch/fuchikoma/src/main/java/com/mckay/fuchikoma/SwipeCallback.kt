package com.mckay.fuchikoma

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.TypedValue
import androidx.recyclerview.widget.ItemTouchHelper
import androidx.recyclerview.widget.RecyclerView
import kotlin.math.abs
import kotlin.math.min

/** Commit-only swipe with a fixed dp threshold and a painted backing.
 *
 * ItemTouchHelper cannot "reveal and hold" a tray the way the web rows do;
 * a swipe either commits (onSwiped) or snaps back. So RIGHT commits the
 * row's primary action and LEFT commits "open the secondary sheet", and the
 * fragment snaps the row back with notifyItemChanged. The threshold is a
 * fixed distance (FuchikomaConfig.SWIPE_COMMIT_DP) rather than a fraction
 * of the row: unfolded, the trifold's rows are ~3 phone widths, folded they
 * are one — a fraction is a whole-thumb drag on one and a twitch on the
 * other. Escape velocity is raised for the same reason: a quick flick on a
 * big screen must not complete a task by accident. */
class SwipeCallback(
    private val recycler: RecyclerView,
    private val dirsFor: (Int) -> Int,                 // ItemTouchHelper.LEFT|RIGHT bitmask per position
    private val rightLabel: (Int) -> String,
    private val leftLabel: (Int) -> String,
    private val rightColor: Int,
    private val leftColor: Int,
    private val onSwipe: (Int, Int) -> Unit,           // (position, direction)
) : ItemTouchHelper.SimpleCallback(0, ItemTouchHelper.LEFT or ItemTouchHelper.RIGHT) {

    private val commitPx = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, FuchikomaConfig.SWIPE_COMMIT_DP, recycler.resources.displayMetrics)
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 14f, recycler.resources.displayMetrics)
        typeface = android.graphics.Typeface.MONOSPACE
    }
    private val pad = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, 16f, recycler.resources.displayMetrics)

    override fun getMovementFlags(rv: RecyclerView, vh: RecyclerView.ViewHolder): Int {
        val pos = vh.bindingAdapterPosition
        if (pos == RecyclerView.NO_POSITION) return 0
        return makeMovementFlags(0, dirsFor(pos))
    }

    override fun onMove(rv: RecyclerView, vh: RecyclerView.ViewHolder, t: RecyclerView.ViewHolder) = false

    override fun getSwipeThreshold(vh: RecyclerView.ViewHolder): Float {
        val w = vh.itemView.width.toFloat().coerceAtLeast(1f)
        return min(0.9f, commitPx / w)
    }

    override fun getSwipeEscapeVelocity(defaultValue: Float) = defaultValue * 6f
    override fun getSwipeVelocityThreshold(defaultValue: Float) = defaultValue * 6f

    override fun onSwiped(vh: RecyclerView.ViewHolder, direction: Int) {
        val pos = vh.bindingAdapterPosition
        if (pos != RecyclerView.NO_POSITION) onSwipe(pos, direction)
    }

    override fun onChildDraw(c: Canvas, rv: RecyclerView, vh: RecyclerView.ViewHolder,
                             dX: Float, dY: Float, state: Int, active: Boolean) {
        val v = vh.itemView
        val pos = vh.bindingAdapterPosition
        if (dX != 0f && pos != RecyclerView.NO_POSITION) {
            val right = dX > 0
            paint.color = if (right) rightColor else leftColor
            // Backing brightens as the drag approaches the commit distance.
            paint.alpha = (255 * min(1f, abs(dX) / commitPx)).toInt().coerceIn(60, 255)
            val rect = if (right) RectF(v.left.toFloat(), v.top.toFloat(), v.left + dX, v.bottom.toFloat())
                       else RectF(v.right + dX, v.top.toFloat(), v.right.toFloat(), v.bottom.toFloat())
            c.drawRect(rect, paint)
            val label = if (right) rightLabel(pos) else leftLabel(pos)
            val y = v.top + v.height / 2f + text.textSize / 3f
            if (right) c.drawText(label, v.left + pad, y, text)
            else c.drawText(label, v.right - pad - text.measureText(label), y, text)
        }
        super.onChildDraw(c, rv, vh, dX, dY, state, active)
    }
}
