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
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val TAG = "Neg1n"
private const val RECONCILE_DELAY_MS = 3000L

/** Opened by tapping the complication: the current block's not-yet-done
 * rituals as a swipeable list. Swipe RIGHT completes one — relays the
 * action to the phone (see PhoneMessenger/RitualCompleter's doc comments
 * for why it's phone-relayed rather than a direct watch HTTP call) and
 * removes the row optimistically; since the relay is fire-and-forget with
 * no ack, every swipe schedules a delayed reload() to reconcile with
 * ground truth regardless of apparent success.
 *
 * Also fires a "/neg1n_sync_now" request to the phone on open, then reloads
 * after a short delay — so opening the list is close to instant-fresh
 * rather than waiting on the phone's own ~15min periodic timer. */
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

        ItemTouchHelper(object : ItemTouchHelper.SimpleCallback(0, ItemTouchHelper.RIGHT) {
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

        reload() // whatever's cached, immediately
        CoroutineScope(Dispatchers.Main).launch {
            val sent = PhoneMessenger.send(applicationContext, "/neg1n_sync_now")
            Log.i(TAG, "RitualListActivity: sync-now request sent=$sent")
            if (sent) {
                delay(RECONCILE_DELAY_MS)
                reload() // pick up whatever the phone just pushed
            }
        }
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
            val sent = PhoneMessenger.send(applicationContext, "/neg1n_complete", tag)
            Log.i(TAG, "RitualListActivity: complete request tag=$tag sent=$sent")
            if (!sent) {
                Toast.makeText(this@RitualListActivity, "phone unreachable", Toast.LENGTH_SHORT).show()
            }
            requestComplicationUpdate()
            // No ack from the relay either way — reconcile with ground
            // truth after giving the phone time to do the real work
            // (fetch backend /complete, push the result) rather than
            // trusting the optimistic removal above.
            delay(RECONCILE_DELAY_MS)
            reload()
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
