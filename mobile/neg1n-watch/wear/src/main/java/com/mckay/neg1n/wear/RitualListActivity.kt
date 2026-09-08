package com.mckay.neg1n.wear

import android.app.Activity
import android.content.ComponentName
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.recyclerview.widget.ItemTouchHelper
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

private const val TAG = "Neg1n"

/** Opened by tapping the complication: the current block's not-yet-done
 * rituals as a swipeable list. Swipe left completes one — calls
 * RitualCompleter (direct HTTP from the watch, see its doc comment) and
 * removes the row optimistically; on failure the row is restored by
 * reloading the authoritative list rather than trying to re-insert it at
 * the right spot. */
class RitualListActivity : Activity() {

    private lateinit var adapter: RitualAdapter
    private lateinit var emptyText: TextView
    private val labelOf = Neg1nConfig.RITUALS.associate { (tag, label, _) -> tag to label }
    private val colorOf: (String) -> Int = { tag ->
        Neg1nConfig.RITUALS.find { it.first == tag }?.third ?: Neg1nConfig.NOT_DONE_COLOR
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_ritual_list)

        emptyText = findViewById(R.id.emptyText)
        adapter = RitualAdapter(colorOf)
        val recycler = findViewById<RecyclerView>(R.id.ritualList)
        recycler.layoutManager = LinearLayoutManager(this)
        recycler.adapter = adapter

        ItemTouchHelper(object : ItemTouchHelper.SimpleCallback(0, ItemTouchHelper.LEFT) {
            override fun onMove(rv: RecyclerView, vh: RecyclerView.ViewHolder, target: RecyclerView.ViewHolder) = false
            override fun onSwiped(viewHolder: RecyclerView.ViewHolder, direction: Int) {
                val position = viewHolder.bindingAdapterPosition
                if (position == RecyclerView.NO_POSITION) return
                val tag = adapter.tagAt(position)
                adapter.removeAt(position)
                updateEmptyState()
                completeRitual(tag)
            }
        }).attachToRecyclerView(recycler)

        reload()
    }

    private fun reload() {
        CoroutineScope(Dispatchers.Main).launch {
            val status = DataLayerReader.readLatest(applicationContext)
            val items = status.notDone.mapNotNull { tag -> labelOf[tag]?.let { tag to it } }
            Log.i(TAG, "RitualListActivity: loaded block=${status.block} not_done=${status.notDone} " +
                "endpoint=${status.endpoint} -> ${items.size} row(s)")
            adapter.setItems(items)
            updateEmptyState()
        }
    }

    private fun completeRitual(tag: String) {
        CoroutineScope(Dispatchers.Main).launch {
            RitualCompleter.complete(applicationContext, tag).fold(
                onSuccess = {
                    Log.i(TAG, "RitualListActivity: completed $tag")
                    requestComplicationUpdate()
                },
                onFailure = { e ->
                    Log.e(TAG, "RitualListActivity: failed to complete $tag", e)
                    Toast.makeText(this@RitualListActivity, "failed: ${e.message}", Toast.LENGTH_SHORT).show()
                    reload() // restore the true list rather than guess where the row goes back
                },
            )
        }
    }

    private fun updateEmptyState() {
        emptyText.visibility = if (adapter.itemCount == 0) View.VISIBLE else View.GONE
    }

    private fun requestComplicationUpdate() {
        val requester = ComplicationDataSourceUpdateRequester.create(
            applicationContext, ComponentName(applicationContext, Neg1nComplicationService::class.java))
        requester.requestUpdateAll()
    }
}
