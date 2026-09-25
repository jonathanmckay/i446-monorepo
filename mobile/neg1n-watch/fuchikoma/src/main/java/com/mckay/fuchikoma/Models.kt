package com.mckay.fuchikoma

import org.json.JSONArray
import org.json.JSONObject

// Pure JSON <-> model code, kept free of Android types so it runs under plain
// JUnit (see src/test). Field names mirror the Flask payloads exactly.

/** One row of dtd's /api/tasks. */
data class DtdTask(
    val id: String,
    val raw: String,            // full Todoist content incl. (N)/[N]/{N} — what /api/done and /api/start take
    val title: String,          // annotation-stripped display text
    val est: Int?,              // (N) minutes
    val points: Int?,           // [N] or {N}
    val color: String,          // "#rrggbb" per domain, resolved server-side
    val domain: String,
    val recurring: Boolean,
    val variablePrompt: Boolean, // ask for a value before completing (variable habits)
)

data class DtdSummary(val points: Int, val done: Int)

data class DelayOptions(val blocks: List<Pair<String, Int>>, val minutes: List<Int>)  // (glyph, hour)

/** One row of janus's /api/timeline. type ∈ divider | gap | entry | event. */
data class TimelineRow(
    val type: String,
    val label: String = "",        // divider
    val start: String = "",        // HH:MM (entry/gap/event)
    val end: String = "",          // HH:MM or "now" for the running entry
    val minutes: Int = 0,
    // entry
    val id: String = "",
    val desc: String = "",
    val project: String = "",
    val color: String = "",
    val tags: List<String> = emptyList(),
    val running: Boolean = false,
    val logged: Boolean = false,
    val points: Int? = null,
    // event
    val title: String = "",
    val isPast: Boolean = false,
    val started: Boolean = false,
    val startIso: String = "",
    val endIso: String = "",
) {
    val isEntry get() = type == "entry"
    val isGap get() = type == "gap"
    val isEvent get() = type == "event"
    val isDivider get() = type == "divider"

    /** Right-swipe (commit) allowed? Mirrors the web: an already-logged entry
     * is inert so a reload never invites a re-swipe; dividers do nothing. */
    val canSwipeRight get() = when (type) {
        "entry" -> running || !logged
        "gap", "event" -> true
        else -> false
    }
    /** Left-swipe (edit/split) is for Toggl entries only — a gap or calendar
     * event isn't a Toggl entry yet. */
    val canSwipeLeft get() = isEntry

    /** What the right-swipe backing says it will do — the web's swipe label. */
    fun rightLabel(): String = when {
        isEvent -> if (isPast) "log 分 (${minutes}m)" else if (started) "resume tracking" else "start tracking"
        isGap -> "fill"
        running -> if (points != null) "done ✓ (${points}分)" else "done ✓ (stop + log)"
        else -> if (points != null) "log ${points}分" else "neon log 分 (${minutes}m)"
    }
}

data class Timeline(val rows: List<TimelineRow>, val trackedMin: Int, val points: Int?, val date: String)

object Parsers {
    private fun JSONObject.optIntOrNull(key: String): Int? =
        if (isNull(key) || !has(key)) null else optInt(key)

    private fun JSONArray?.strings(): List<String> =
        this?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: emptyList()

    fun tasks(json: JSONObject): List<DtdTask> {
        val arr = json.optJSONArray("tasks") ?: return emptyList()
        return (0 until arr.length()).map { i ->
            val t = arr.getJSONObject(i)
            DtdTask(
                id = t.optString("id"),
                raw = t.optString("raw"),
                title = t.optString("title").ifBlank { t.optString("raw") },
                est = t.optIntOrNull("est"),
                points = t.optIntOrNull("points"),
                color = t.optString("color", "#e6e6e0"),
                domain = t.optString("domain"),
                recurring = t.optBoolean("recurring", false),
                variablePrompt = t.optBoolean("variablePrompt", false),
            )
        }
    }

    fun summary(json: JSONObject): DtdSummary {
        val s = json.optJSONObject("summary") ?: json
        return DtdSummary(s.optInt("points", 0), s.optInt("done", 0))
    }

    fun delayOptions(json: JSONObject): DelayOptions {
        val b = json.optJSONArray("blocks")
        val blocks = b?.let { a -> (0 until a.length()).map { i ->
            val o = a.getJSONObject(i); o.optString("glyph") to o.optInt("hour") } } ?: emptyList()
        val m = json.optJSONArray("minutes")
        val minutes = m?.let { a -> (0 until a.length()).map { a.getInt(it) } } ?: emptyList()
        return DelayOptions(blocks, minutes)
    }

    fun timeline(json: JSONObject): Timeline {
        val arr = json.optJSONArray("rows") ?: JSONArray()
        val rows = (0 until arr.length()).map { i ->
            val r = arr.getJSONObject(i)
            TimelineRow(
                type = r.optString("type"),
                label = r.optString("label"),
                start = r.optString("start"),
                end = r.optString("end"),
                minutes = r.optInt("minutes", 0),
                id = r.opt("id")?.toString() ?: "",
                desc = r.optString("desc"),
                project = r.optString("project"),
                color = r.optString("color"),
                tags = r.optJSONArray("tags").strings(),
                running = r.optBoolean("running", false),
                logged = r.optBoolean("logged", false),
                points = r.optIntOrNull("points"),
                title = r.optString("title"),
                isPast = r.optBoolean("is_past", false),
                started = r.optBoolean("started", false),
                startIso = r.optString("start_iso"),
                endIso = r.optString("end_iso"),
            )
        }
        return Timeline(rows, json.optInt("tracked_min", 0), json.optIntOrNull("points"), json.optString("date"))
    }
}

/** Request bodies, byte-for-byte what the web pages send. */
object Bodies {
    /** /api/done. A variable habit's typed value rides in the content, exactly
     * as the web's submitVal does (title + " " + value); blank = skip. */
    fun done(t: DtdTask, value: String? = null): JSONObject {
        val v = value?.filter { it.isDigit() }.orEmpty()
        val content = if (v.isNotEmpty()) "${t.title} $v" else t.raw
        return JSONObject().put("id", t.id).put("content", content)
    }
    fun start(t: DtdTask) = JSONObject().put("content", t.raw)
    fun byId(t: DtdTask) = JSONObject().put("id", t.id)
    fun delayHour(t: DtdTask, hour: Int) = JSONObject().put("id", t.id).put("hour", hour)
    fun delayMinutes(t: DtdTask, minutes: Int) = JSONObject().put("id", t.id).put("minutes", minutes)
    fun add(content: String) = JSONObject().put("content", content)

    fun log(r: TimelineRow) = JSONObject().put("id", r.id).put("desc", r.desc)
        .put("minutes", r.minutes).put("project", r.project).put("tags", JSONArray(r.tags))
    fun doneCurrent(r: TimelineRow) = JSONObject().put("id", r.id).put("desc", r.desc).put("project", r.project)
    fun convertEvent(r: TimelineRow) = JSONObject().put("title", r.title).put("start_iso", r.startIso)
        .put("end_iso", r.endIso).put("code", r.project).put("is_past", r.isPast)
    fun fill(desc: String, start: String, end: String) =
        JSONObject().put("desc", desc).put("start", start).put("end", end)
    fun edit(id: String, desc: String, start: String, end: String, project: String) =
        JSONObject().put("id", id).put("desc", desc).put("start", start).put("end", end).put("project", project)
    fun split(id: String, mode: String) = JSONObject().put("id", id).put("mode", mode)
}
