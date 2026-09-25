package com.mckay.fuchikoma

import android.graphics.Color
import android.os.Bundle
import android.text.InputType
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.ItemTouchHelper
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.floatingactionbutton.FloatingActionButton
import com.google.android.material.snackbar.Snackbar
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** dtd: today's task list. Right-swipe = complete (real /did via the server:
 * closes Todoist + writes Neon). Left-swipe = start / +1d / ⏰ delay sheet.
 * FAB = quick-add. Same contract as tools/dtd/dtd.py's web page. */
class DtdFragment : Fragment(), Surface {

    private val api = Api()
    private val tasks = mutableListOf<DtdTask>()
    private val inFlight = mutableSetOf<String>()   // ids with a POST outstanding — no double swipes
    private var summary = DtdSummary(0, 0)
    private var lastError: String? = null
    private var loadedOnce = false
    private lateinit var adapter: TaskAdapter
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var empty: TextView

    private fun base() = FuchikomaConfig.dtdBase(requireContext())

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, saved: Bundle?): View =
        inflater.inflate(R.layout.fragment_list, container, false)

    override fun onViewCreated(view: View, saved: Bundle?) {
        val list = view.findViewById<RecyclerView>(R.id.list)
        swipe = view.findViewById(R.id.swipeRefresh)
        empty = view.findViewById(R.id.empty)
        empty.text = getString(R.string.empty_dtd)
        adapter = TaskAdapter(tasks)
        list.layoutManager = LinearLayoutManager(requireContext())
        list.adapter = adapter
        val green = ContextCompat.getColor(requireContext(), R.color.swipe_done)
        val grey = ContextCompat.getColor(requireContext(), R.color.swipe_actions)
        ItemTouchHelper(SwipeCallback(list,
            dirsFor = { pos -> if (tasks[pos].id in inFlight) 0 else ItemTouchHelper.LEFT or ItemTouchHelper.RIGHT },
            rightLabel = { pos -> tasks[pos].points?.let { "done  +$it 分" } ?: "done" },
            leftLabel = { "▶ start · +1d · ⏰" },
            rightColor = green, leftColor = grey,
        ) { pos, dir ->
            if (dir == ItemTouchHelper.RIGHT) onCompleteSwipe(pos)
            else { adapter.notifyItemChanged(pos); openActions(pos) }
        }).attachToRecyclerView(list)
        swipe.setOnRefreshListener { load(refresh = true) }
        view.findViewById<FloatingActionButton>(R.id.fab).setOnClickListener { openAdd() }
    }

    override fun onShown() { pushHeader(); if (!loadedOnce) load(refresh = false) }
    override fun reload() { load(refresh = true) }

    private fun pushHeader() {
        val a = activity as? MainActivity ?: return
        val title = "${summary.points}分 · dtd · ${summary.done} done · ${tasks.size} left"
        a.setHeader(this, title, lastError?.let { "⚠ $it" } ?: base())
    }

    private fun load(refresh: Boolean) {
        swipe.isRefreshing = true
        lifecycleScope.launch {
            val url = base() + "/api/tasks" + if (refresh) "?refresh=1" else ""
            when (val r = withContext(Dispatchers.IO) { api.get(url) }) {
                is ApiResult.Ok -> {
                    loadedOnce = true
                    lastError = null
                    tasks.clear(); tasks.addAll(Parsers.tasks(r.json))
                    summary = Parsers.summary(r.json)
                    adapter.notifyDataSetChanged()
                }
                is ApiResult.Error -> lastError = r.message
            }
            swipe.isRefreshing = false
            empty.visibility = if (tasks.isEmpty() && loadedOnce) View.VISIBLE else View.GONE
            pushHeader()
        }
    }

    // ── complete ─────────────────────────────────────────────────────────
    private fun onCompleteSwipe(pos: Int) {
        val t = tasks[pos]
        if (!t.variablePrompt) { complete(pos, t, null); return }
        adapter.notifyItemChanged(pos)  // snap back while we ask
        val input = EditText(requireContext()).apply { inputType = InputType.TYPE_CLASS_NUMBER; hint = "value" }
        AlertDialog.Builder(requireContext()).setTitle(t.title).setMessage("value?").setView(input)
            .setPositiveButton("done") { _, _ -> complete(indexOf(t), t, input.text.toString()) }
            .setNeutralButton("skip") { _, _ -> complete(indexOf(t), t, "") }
            .setNegativeButton("cancel", null).show()
    }

    private fun indexOf(t: DtdTask) = tasks.indexOfFirst { it.id == t.id }

    /** Optimistic: the row leaves immediately (like the web's fly()), but
     * unlike the web a failure puts it BACK with a persistent retry — a
     * vanished row plus a 2-second toast is invisible on a phone. */
    private fun complete(pos: Int, t: DtdTask, value: String?) {
        if (pos < 0 || t.id in inFlight) return
        inFlight += t.id
        removeRow(pos)
        summary = summary.copy(points = summary.points + (t.points ?: 0), done = summary.done + 1)
        pushHeader()
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { api.post(base() + "/api/done", Bodies.done(t, value)) }
            inFlight -= t.id
            when (r) {
                is ApiResult.Ok -> {
                    val pts = t.points ?: 0
                    val msg = when {
                        r.json.optBoolean("closed") -> "+$pts 分 ✓"
                        r.json.optBoolean("future_skipped") -> "+$pts 分 · recurring"
                        else -> "+$pts 分"
                    }
                    snack(msg)
                }
                is ApiResult.Error -> {
                    summary = summary.copy(points = summary.points - (t.points ?: 0), done = summary.done - 1)
                    restoreRow(pos, t)
                    snack("not completed: ${r.message}", retry = { complete(indexOf(t), t, value) })
                }
            }
            pushHeader()
        }
    }

    private fun removeRow(pos: Int) {
        tasks.removeAt(pos); adapter.notifyItemRemoved(pos)
        empty.visibility = if (tasks.isEmpty()) View.VISIBLE else View.GONE
    }

    private fun restoreRow(pos: Int, t: DtdTask) {
        if (indexOf(t) >= 0) return
        val at = pos.coerceIn(0, tasks.size)
        tasks.add(at, t); adapter.notifyItemInserted(at)
        empty.visibility = View.GONE
    }

    // ── left-swipe sheet: start / +1d / ⏰ ────────────────────────────────
    private fun openActions(pos: Int) {
        val t = tasks[pos]
        val sheet = BottomSheetDialog(requireContext())
        val v = layoutInflater.inflate(R.layout.sheet_actions, null)
        v.findViewById<TextView>(R.id.sheetTitle).text = t.title
        v.findViewById<Button>(R.id.actStart).setOnClickListener { sheet.dismiss(); start(t) }
        v.findViewById<Button>(R.id.actDay).setOnClickListener { sheet.dismiss(); delay(t, "/api/delay-day", Bodies.byId(t)) { d -> "🗓 → " + d.optString("target_date", "tomorrow") } }
        v.findViewById<Button>(R.id.actBlock).setOnClickListener { showDelayOptions(v, sheet, t) }
        sheet.setContentView(v); sheet.show()
    }

    private fun start(t: DtdTask) {
        lifecycleScope.launch {
            when (val r = withContext(Dispatchers.IO) { api.post(base() + "/api/start", Bodies.start(t)) }) {
                is ApiResult.Ok -> snack("▶ " + r.json.optString("clean", t.title) +
                    r.json.optString("project").let { if (it.isNotBlank()) " → $it" else "" })
                is ApiResult.Error -> snack("not started: ${r.message}")
            }
        }
    }

    /** Delays hide the task until its delayed time, so the row leaves like a
     * completion does (web: "delaying a day/block hides the task"). */
    private fun delay(t: DtdTask, path: String, body: JSONObject, toast: (JSONObject) -> String) {
        val pos = indexOf(t)
        if (pos < 0 || t.id in inFlight) return
        inFlight += t.id
        removeRow(pos); pushHeader()
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { api.post(base() + path, body) }
            inFlight -= t.id
            when (r) {
                is ApiResult.Ok -> snack(toast(r.json))
                is ApiResult.Error -> { restoreRow(pos, t); snack("not delayed: ${r.message}", retry = { delay(t, path, body, toast) }) }
            }
            pushHeader()
        }
    }

    private fun showDelayOptions(v: View, sheet: BottomSheetDialog, t: DtdTask) {
        val box = v.findViewById<LinearLayout>(R.id.delayOptions)
        box.removeAllViews(); box.visibility = View.VISIBLE
        box.addView(TextView(requireContext()).apply { text = "loading…"; setTextColor(Color.GRAY) })
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { api.get(base() + "/api/delay-options") }
            box.removeAllViews()
            if (r !is ApiResult.Ok) { box.addView(TextView(requireContext()).apply { text = "could not load options" }); return@launch }
            val opts = Parsers.delayOptions(r.json)
            if (opts.blocks.isEmpty() && opts.minutes.isEmpty()) box.addView(TextView(requireContext()).apply { text = "nothing later today" })
            val row = LinearLayout(requireContext()).apply { orientation = LinearLayout.HORIZONTAL }
            fun btn(label: String, onClick: () -> Unit) = Button(requireContext()).apply {
                text = label; layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                setOnClickListener { sheet.dismiss(); onClick() }
            }
            for ((glyph, hour) in opts.blocks) {
                row.addView(btn("$glyph ${"%02d".format(hour)}:00") {
                    delay(t, "/api/delay-hour", Bodies.delayHour(t, hour)) { "⏰ → ${"%02d".format(hour)}:00" }
                })
            }
            box.addView(row)
            val row2 = LinearLayout(requireContext()).apply { orientation = LinearLayout.HORIZONTAL }
            for (m in opts.minutes) {
                val label = if (m % 60 == 0) "+${m / 60}h" else "+${m}m"
                row2.addView(btn(label) { delay(t, "/api/delay-minutes", Bodies.delayMinutes(t, m)) { "⏰ → $label" } })
            }
            box.addView(row2)
        }
    }

    // ── add ──────────────────────────────────────────────────────────────
    private fun openAdd() {
        val input = EditText(requireContext()).apply { hint = "task (N) [pts] @code"; inputType = InputType.TYPE_CLASS_TEXT }
        AlertDialog.Builder(requireContext()).setTitle(getString(R.string.add_task)).setView(input)
            .setPositiveButton("add") { _, _ ->
                val content = input.text.toString().trim()
                if (content.isEmpty()) return@setPositiveButton
                lifecycleScope.launch {
                    when (val r = withContext(Dispatchers.IO) { api.post(base() + "/api/add", Bodies.add(content)) }) {
                        is ApiResult.Ok -> { snack("+ added"); load(refresh = true) }
                        is ApiResult.Error -> snack("not added: ${r.message}")
                    }
                }
            }
            .setNegativeButton("cancel", null).show()
    }

    private fun snack(msg: String, retry: (() -> Unit)? = null) {
        val v = view ?: return
        val s = Snackbar.make(v, msg, if (retry != null) Snackbar.LENGTH_INDEFINITE else Snackbar.LENGTH_SHORT)
        if (retry != null) s.setAction("retry") { retry() }
        s.show()
    }
}

class TaskAdapter(private val items: List<DtdTask>) : RecyclerView.Adapter<TaskAdapter.VH>() {
    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val bar: View = v.findViewById(R.id.colorBar)
        val title: TextView = v.findViewById(R.id.title)
        val meta: TextView = v.findViewById(R.id.meta)
    }
    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_task, parent, false))
    override fun getItemCount() = items.size
    override fun onBindViewHolder(h: VH, pos: Int) {
        val t = items[pos]
        val c = try { Color.parseColor(t.color) } catch (e: Exception) { Color.LTGRAY }
        h.bar.setBackgroundColor(c)
        h.title.text = (if (t.recurring) "↻ " else "") + t.title
        h.title.setTextColor(c)
        // Right-justified (time)[value] like the terminal and the web.
        h.meta.text = listOfNotNull(t.est?.let { "($it)" }, t.points?.let { "[$it]" }).joinToString(" ")
    }
}
