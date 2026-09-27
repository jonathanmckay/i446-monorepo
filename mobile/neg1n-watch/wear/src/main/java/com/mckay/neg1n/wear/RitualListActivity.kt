package com.mckay.neg1n.wear

import android.app.Activity
import android.app.RemoteInput
import android.content.ActivityNotFoundException
import android.content.Intent
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
import androidx.wear.input.RemoteInputIntentHelper
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val TAG = "Neg1n"
private const val RECONCILE_DELAY_MS = 3000L
private const val GOAL_RITUAL_TAG = "-1g"
private const val GOAL_INPUT_KEY = "neg1n_goal"
private const val REQ_GOAL_INPUT = 41

/** Opened by tapping the complication: the current block's not-yet-done
 * rituals as a swipeable list. Swipe RIGHT completes one — relays the
 * action to the phone (see PhoneMessenger/RitualCompleter's doc comments
 * for why it's phone-relayed rather than a direct watch HTTP call) and
 * removes the row optimistically; since the relay is fire-and-forget with
 * no ack, every swipe schedules a delayed reload() to reconcile with
 * ground truth regardless of apparent success.
 *
 * That reconcile reload() races the phone's actual completion work (see
 * PendingCompletions' doc comment) and can lose — every reload() therefore
 * filters through `pendingCompletions`, a local "already swiped this
 * session" overlay, so a tag we've completed can never revert to
 * not-done in this activity's list, even if the synced Data Layer item is
 * still momentarily stale.
 *
 * Also fires a "/neg1n_sync_now" request to the phone on open, then reloads
 * after a short delay — so opening the list is close to instant-fresh
 * rather than waiting on the phone's own ~15min periodic timer. */
class RitualListActivity : Activity() {

    private lateinit var adapter: RitualAdapter
    private lateinit var emptyText: TextView
    private lateinit var pendingCompletions: PendingCompletions
    private var currentBlock: String? = null
    private val labelOf = Neg1nConfig.RITUALS.associate { (tag, label, _) -> tag to label }
    private val colorOf: (String) -> Int = { tag ->
        Neg1nConfig.RITUALS.find { it.first == tag }?.third ?: Neg1nConfig.NOT_DONE_COLOR
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_ritual_list)

        emptyText = findViewById(R.id.emptyText)
        pendingCompletions = PendingCompletions.fromPrefs(applicationContext)
        // Tap (not swipe) on the -1g row opens Wear's system text entry
        // (keyboard + voice dictation) for this block's goal -- see
        // openGoalInput/onActivityResult. Other rows ignore taps.
        adapter = RitualAdapter(colorOf) { tag -> if (tag == GOAL_RITUAL_TAG) openGoalInput() }
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
            // readLatest already applies the persisted swipe overlay.
            val status = DataLayerReader.readLatest(applicationContext)
            currentBlock = status.block
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
            if (sent) {
                // Persisted, so a reopened list and the complication bar
                // keep showing it done until the remote confirms — only
                // when the relay was actually delivered; an undeliverable
                // swipe must come straight back (reload below), not hide
                // for the overlay's TTL.
                pendingCompletions.markCompleted(tag, currentBlock)
            } else {
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

    /** Wear RemoteInput: the platform's own text-entry sheet (keyboard,
     * voice, scribble), so dictation comes for free. Result arrives in
     * onActivityResult (plain Activity, no AndroidX result API here). */
    private fun openGoalInput() {
        val remoteInput = RemoteInput.Builder(GOAL_INPUT_KEY)
            .setLabel("-1g goal")
            .build()
        val intent = RemoteInputIntentHelper.createActionRemoteInputIntent()
        RemoteInputIntentHelper.putRemoteInputsExtra(intent, listOf(remoteInput))
        try {
            @Suppress("DEPRECATION")
            startActivityForResult(intent, REQ_GOAL_INPUT)
        } catch (e: ActivityNotFoundException) {
            Log.e(TAG, "RitualListActivity: no remote input activity", e)
            Toast.makeText(this, "no text input available", Toast.LENGTH_SHORT).show()
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_GOAL_INPUT || resultCode != RESULT_OK || data == null) return
        val text = RemoteInput.getResultsFromIntent(data)?.getCharSequence(GOAL_INPUT_KEY)?.toString()?.trim().orEmpty()
        if (text.isEmpty()) return
        setGoal(text)
    }

    /** Relays the goal text to the phone ("/neg1n_goal" -> SetGoalWorker ->
     * POST /api/neg1n/goal on Ix), which also closes the -1g ritual
     * server-side -- so the row is treated like a swipe-complete on the
     * watch: optimistic removal + persisted overlay, reconciled after a
     * delay. Only after a delivered relay: an undeliverable goal must come
     * straight back, not hide for the overlay's TTL. */
    private fun setGoal(text: String) {
        CoroutineScope(Dispatchers.Main).launch {
            val sent = PhoneMessenger.send(applicationContext, "/neg1n_goal", text)
            Log.i(TAG, "RitualListActivity: goal request sent=$sent text=$text")
            if (sent) {
                val pos = (0 until adapter.itemCount).firstOrNull { adapter.tagAt(it) == GOAL_RITUAL_TAG }
                if (pos != null) { adapter.removeAt(pos); updateEmptyState() }
                pendingCompletions.markCompleted(GOAL_RITUAL_TAG, currentBlock)
                Toast.makeText(this@RitualListActivity, "goal: $text", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this@RitualListActivity, "phone unreachable", Toast.LENGTH_SHORT).show()
            }
            requestComplicationUpdate()
            // The server's goal call can take 10-20s (Todoist + did-fast);
            // give it longer than a plain completion before reconciling.
            delay(RECONCILE_DELAY_MS * 4)
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
