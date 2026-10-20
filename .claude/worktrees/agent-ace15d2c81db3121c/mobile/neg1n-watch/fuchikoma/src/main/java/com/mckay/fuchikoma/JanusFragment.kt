package com.mckay.fuchikoma

import android.graphics.Color
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.ItemTouchHelper
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.floatingactionbutton.FloatingActionButton
import com.google.android.material.snackbar.Snackbar
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** janus: today's timeline (Toggl entries, gaps, calendar events, 地支
 * dividers). Right-swipe: entry → log 分, running → stop+log, event → convert
 * to a Toggl entry, gap → fill dialog. Left-swipe on an entry → edit/split.
 * Every action reloads the whole timeline (no optimism — the server owns the
 * ledger that says what is already logged). Same contract as
 * tools/janus/mobile.py's web page. */
class JanusFragment : Fragment(), Surface {

    private val api = Api()
    private val rows = mutableListOf<TimelineRow>()
    private var tl: Timeline? = null
    private var lastError: String? = null
    private var loadedOnce = false
    private var busy = false   // one action at a time; did-fast writes aren't idempotent
    private lateinit var adapter: TimelineAdapter
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var empty: TextView

    private fun base() = FuchikomaConfig.janusBase(requireContext())

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, saved: Bundle?): View =
        inflater.inflate(R.layout.fragment_list, container, false)

    override fun onViewCreated(view: View, saved: Bundle?) {
        val list = view.findViewById<RecyclerView>(R.id.list)
        swipe = view.findViewById(R.id.swipeRefresh)
        empty = view.findViewById(R.id.empty)
        empty.text = getString(R.string.empty_janus)
        adapter = TimelineAdapter(rows)
        list.layoutManager = LinearLayoutManager(requireContext())
        list.adapter = adapter
        val green = ContextCompat.getColor(requireContext(), R.color.swipe_done)
        val purple = ContextCompat.getColor(requireContext(), R.color.swipe_edit)
        ItemTouchHelper(SwipeCallback(list,
            dirsFor = { pos ->
                if (busy) 0 else {
                    val r = rows[pos]
                    (if (r.canSwipeRight) ItemTouchHelper.RIGHT else 0) or (if (r.canSwipeLeft) ItemTouchHelper.LEFT else 0)
                }
            },
            rightLabel = { pos -> rows[pos].rightLabel() },
            leftLabel = { "edit / split" },
            rightColor = green, leftColor = purple,
        ) { pos, dir ->
            val r = rows[pos]
            adapter.notifyItemChanged(pos)   // commit-only helper: always snap back, the reload redraws
            if (dir == ItemTouchHelper.RIGHT) onRight(r) else openEdit(r)
        }).attachToRecyclerView(list)
        swipe.setOnRefreshListener { load() }
        view.findViewById<FloatingActionButton>(R.id.fab).setOnClickListener { openFill(null) }
    }

    override fun onShown() { pushHeader(); if (!loadedOnce) load() }
    override fun reload() { load() }

    private fun pushHeader() {
        val a = activity as? MainActivity ?: return
        val t = tl
        val pts = t?.points?.let { "${it}分" } ?: "–分"
        val tracked = t?.let { "%d:%02d".format(it.trackedMin / 60, it.trackedMin % 60) } ?: "0:00"
        a.setHeader(this, "$pts · janus · $tracked tracked", lastError?.let { "⚠ $it" } ?: base())
    }

    private fun load() {
        swipe.isRefreshing = true
        lifecycleScope.launch {
            when (val r = withContext(Dispatchers.IO) { api.get(base() + "/api/timeline") }) {
                is ApiResult.Ok -> {
                    loadedOnce = true; lastError = null
                    val t = Parsers.timeline(r.json); tl = t
                    rows.clear(); rows.addAll(t.rows); adapter.notifyDataSetChanged()
                }
                is ApiResult.Error -> lastError = r.message
            }
            swipe.isRefreshing = false
            empty.visibility = if (rows.isEmpty() && loadedOnce) View.VISIBLE else View.GONE
            pushHeader()
        }
    }

    // ── right-swipe commits ──────────────────────────────────────────────
    private fun onRight(r: TimelineRow) {
        when {
            r.isGap -> openFill(r)
            r.isEvent -> act("/api/convert-event", Bodies.convertEvent(r)) { d ->
                if (d.optString("mode") == "logged") "+${r.minutes}m → ${d.optString("step", "neon")} ✓" else "tracking started ✓" }
            r.running -> act("/api/done-current", Bodies.doneCurrent(r)) { d -> "stopped + logged → ${d.optString("step", "neon")} ✓" }
            r.logged -> snack("already logged")
            else -> act("/api/log", Bodies.log(r)) { d ->
                if (d.optBoolean("already")) "already logged" else {
                    val extra = d.optJSONArray("tag_steps")?.let { a -> (0 until a.length()).joinToString(", ") { a.getString(it) } }.orEmpty()
                    "+${r.minutes}m → ${d.optString("step", "neon")}" + (if (extra.isNotEmpty()) " + $extra" else "") + " ✓"
                } }
        }
    }

    /** One POST, then a full reload. `needs_agent` is the server saying it
     * has no route for this description — the web sends you to desktop /did. */
    private fun act(path: String, body: org.json.JSONObject, toast: (org.json.JSONObject) -> String) {
        if (busy) return
        busy = true
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { api.post(base() + path, body) }
            busy = false
            when (r) {
                is ApiResult.Ok -> { snack(toast(r.json)); load() }
                is ApiResult.Error -> snack(if (r.message.contains("needs_agent")) "no route — use /did on desktop" else r.message)
            }
        }
    }

    // ── fill a gap (or FAB: fill "now") ─────────────────────────────────
    private fun openFill(gap: TimelineRow?) {
        val v = layoutInflater.inflate(R.layout.dialog_fill, null)
        val desc = v.findViewById<EditText>(R.id.desc)
        val ongoing = v.findViewById<CheckBox>(R.id.ongoing)
        val start = v.findViewById<EditText>(R.id.start)
        val end = v.findViewById<EditText>(R.id.end)
        if (gap != null) { start.setText(gap.start); end.setText(gap.end) }
        else { start.setText(SimpleDateFormat("HH:mm", Locale.US).format(Date())); ongoing.isChecked = true; end.isEnabled = false }
        ongoing.setOnCheckedChangeListener { _, on -> end.isEnabled = !on; if (on) end.setText("") }
        AlertDialog.Builder(requireContext()).setTitle(if (gap != null) "fill ${gap.start}–${gap.end}" else "start now").setView(v)
            .setPositiveButton("save") { _, _ ->
                val d = desc.text.toString().trim()
                if (d.isEmpty()) { snack("description required"); return@setPositiveButton }
                act("/api/fill", Bodies.fill(d, start.text.toString().trim(),
                    if (ongoing.isChecked) "" else end.text.toString().trim())) { "saved ✓" }
            }
            .setNegativeButton("cancel", null).show()
    }

    // ── edit / split an entry ────────────────────────────────────────────
    private fun openEdit(r: TimelineRow) {
        val v = layoutInflater.inflate(R.layout.dialog_edit, null)
        val desc = v.findViewById<EditText>(R.id.desc).apply { setText(r.desc) }
        val start = v.findViewById<EditText>(R.id.start).apply { setText(r.start) }
        val end = v.findViewById<EditText>(R.id.end).apply { setText(if (r.running) "" else r.end) }
        val project = v.findViewById<Spinner>(R.id.project)
        val codes = mutableListOf(r.project.ifBlank { "" })
        project.adapter = ArrayAdapter(requireContext(), android.R.layout.simple_spinner_dropdown_item, codes)
        lifecycleScope.launch {
            val res = withContext(Dispatchers.IO) { api.get(base() + "/api/projects") }
            if (res is ApiResult.Ok) {
                val arr = res.json.optJSONArray("codes") ?: return@launch
                val all = (0 until arr.length()).map { arr.getString(it) }.toMutableList()
                if (r.project.isBlank() || r.project !in all) all.add(0, r.project)
                codes.clear(); codes.addAll(all)
                (project.adapter as ArrayAdapter<*>).notifyDataSetChanged()
                project.setSelection(all.indexOf(r.project).coerceAtLeast(0))
            }
        }
        val dlg = AlertDialog.Builder(requireContext()).setTitle("edit ${r.start}–${r.end}").setView(v)
            .setPositiveButton("save") { _, _ ->
                act("/api/edit", Bodies.edit(r.id, desc.text.toString().trim(), start.text.toString().trim(),
                    end.text.toString().trim(), project.selectedItem?.toString().orEmpty())) { "edited ✓" }
            }
            .setNegativeButton("cancel", null).create()
        v.findViewById<Button>(R.id.splitTop).setOnClickListener { dlg.dismiss(); act("/api/split", Bodies.split(r.id, "top")) { "split ▲ ✓" } }
        v.findViewById<Button>(R.id.splitBottom).setOnClickListener { dlg.dismiss(); act("/api/split", Bodies.split(r.id, "bottom")) { "split ▼ ✓" } }
        dlg.show()
    }

    private fun snack(msg: String) { view?.let { Snackbar.make(it, msg, Snackbar.LENGTH_SHORT).show() } }
}

class TimelineAdapter(private val items: List<TimelineRow>) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {
    private class DividerVH(v: View) : RecyclerView.ViewHolder(v) { val label: TextView = v.findViewById(R.id.label) }
    private class RowVH(v: View) : RecyclerView.ViewHolder(v) {
        val title: TextView = v.findViewById(R.id.title)
        val meta: TextView = v.findViewById(R.id.meta)
    }
    override fun getItemViewType(position: Int) = if (items[position].isDivider) 0 else 1
    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inf = LayoutInflater.from(parent.context)
        return if (viewType == 0) DividerVH(inf.inflate(R.layout.item_divider, parent, false))
               else RowVH(inf.inflate(R.layout.item_timeline, parent, false))
    }
    override fun getItemCount() = items.size
    override fun onBindViewHolder(h: RecyclerView.ViewHolder, pos: Int) {
        val r = items[pos]
        if (h is DividerVH) { h.label.text = r.label; return }
        h as RowVH
        val dim = ContextCompat.getColor(h.itemView.context, R.color.fg_dim)
        val fg = ContextCompat.getColor(h.itemView.context, R.color.fg)
        when {
            r.isGap -> { h.title.text = "· empty ·"; h.title.setTextColor(dim) }
            r.isEvent -> { h.title.text = "📅 " + r.title; h.title.setTextColor(parse(r.color, fg)) }
            else -> {
                h.title.text = (if (r.running) "▶ " else if (r.logged) "✓ " else "") + r.desc +
                    (if (r.tags.isNotEmpty()) "  #" + r.tags.joinToString(" #") else "")
                h.title.setTextColor(parse(r.color, fg))
            }
        }
        h.title.alpha = if (r.logged && !r.running) 0.55f else 1f
        h.meta.text = "${r.start}–${r.end} · ${r.minutes}m" + (r.points?.let { " · ${it}分" } ?: "")
    }
    private fun parse(c: String, fallback: Int) = try { Color.parseColor(c) } catch (e: Exception) { fallback }
}
