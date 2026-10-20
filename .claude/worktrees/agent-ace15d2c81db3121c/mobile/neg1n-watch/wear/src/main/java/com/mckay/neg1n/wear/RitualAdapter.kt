package com.mckay.neg1n.wear

import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

/** One row per not-yet-done ritual: a color dot (its bar's color from
 * Neg1nConfig.RITUALS) + label. Swipe-to-complete is wired by the Activity
 * via ItemTouchHelper, not here — this adapter only owns the list and its
 * rendering. */
class RitualAdapter(private val colorOf: (String) -> Int) : RecyclerView.Adapter<RitualAdapter.ViewHolder>() {

    private val items = mutableListOf<Pair<String, String>>() // (tag, label)

    class ViewHolder(itemView: android.view.View) : RecyclerView.ViewHolder(itemView) {
        val dot: android.view.View = itemView.findViewById(R.id.colorDot)
        val label: TextView = itemView.findViewById(R.id.ritualLabel)
    }

    fun setItems(newItems: List<Pair<String, String>>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    fun tagAt(position: Int): String = items[position].first

    fun removeAt(position: Int) {
        items.removeAt(position)
        notifyItemRemoved(position)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context).inflate(R.layout.item_ritual, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val (tag, label) = items[position]
        holder.label.text = label
        holder.dot.setBackgroundColor(colorOf(tag))
    }

    override fun getItemCount(): Int = items.size
}
