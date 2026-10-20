package com.mckay.fuchikoma

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Parsers and request bodies against literal payloads captured from the
 * Flask services (tools/dtd/dtd.py, tools/janus/mobile.py). If a server field
 * is renamed these fail before anything ships to the phone. */
class ModelsTest {

    private val tasksJson = """
    {"ok": true, "tasks": [
      {"id": "6hcW1", "raw": "1 cal (15) [15]", "title": "1 cal", "est": 15, "points": 15,
       "color": "#7dd3fc", "domain": "hcb", "recurring": true, "variablePrompt": false},
      {"id": "6hcW2", "raw": "xk20 (30)", "title": "xk20", "est": 30, "points": null,
       "color": "#f9a8d4", "domain": "xk87", "recurring": false, "variablePrompt": true}
    ], "summary": {"points": 497, "done": 12}}
    """.trimIndent()

    @Test
    fun parses_tasks_with_nullable_points() {
        val tasks = Parsers.tasks(JSONObject(tasksJson))
        assertEquals(2, tasks.size)
        assertEquals(15, tasks[0].points)
        assertNull(tasks[1].points)
        assertTrue(tasks[1].variablePrompt)
        assertEquals("#7dd3fc", tasks[0].color)
        assertEquals(DtdSummary(497, 12), Parsers.summary(JSONObject(tasksJson)))
    }

    @Test
    fun done_body_carries_typed_value_like_the_web() {
        val t = Parsers.tasks(JSONObject(tasksJson))[1]
        // web submitVal: fly(row, line, {...t, raw: v ? (t.title + ' ' + v) : t.title})
        assertEquals("xk20 45", Bodies.done(t, "45m").getString("content"))
        assertEquals("xk20 (30)", Bodies.done(t, "").getString("content"))
        assertEquals("xk20 (30)", Bodies.done(t, null).getString("content"))
        assertEquals("6hcW2", Bodies.done(t).getString("id"))
    }

    @Test
    fun delay_options_shape() {
        val d = Parsers.delayOptions(JSONObject("""{"ok":true,"blocks":[{"glyph":"酉","hour":16},{"glyph":"戌","hour":18}],"minutes":[10,30,60]}"""))
        assertEquals(listOf("酉" to 16, "戌" to 18), d.blocks)
        assertEquals(listOf(10, 30, 60), d.minutes)
        assertEquals(16, Bodies.delayHour(Parsers.tasks(JSONObject(tasksJson))[0], 16).getInt("hour"))
    }

    private val timelineJson = """
    {"ok": true, "date": "2026-09-24", "tracked_min": 512, "points": 497, "rows": [
      {"type": "divider", "label": "巳 08:00"},
      {"type": "gap", "start": "08:00", "end": "08:20", "minutes": 20},
      {"type": "entry", "id": 4565255703, "desc": "0g", "project": "g245", "color": "#c3fc0d",
       "tags": [], "start": "14:48", "end": "14:50", "minutes": 2, "running": false, "logged": true, "points": null},
      {"type": "entry", "id": 4565255999, "desc": "vibing", "project": "i9", "color": "#60a5fa",
       "tags": ["-2"], "start": "14:52", "end": "now", "minutes": 40, "running": true, "logged": false, "points": 20},
      {"type": "event", "title": "LX/JM", "project": "m5x2", "color": "#fbbf24", "start": "12:30", "end": "13:00",
       "minutes": 30, "is_past": true, "started": false,
       "start_iso": "2026-09-24T12:30:00-07:00", "end_iso": "2026-09-24T13:00:00-07:00"}
    ]}
    """.trimIndent()

    @Test
    fun parses_timeline_rows_and_numeric_ids() {
        val tl = Parsers.timeline(JSONObject(timelineJson))
        assertEquals(5, tl.rows.size)
        assertEquals(512, tl.trackedMin)
        assertEquals(497, tl.points)
        val entry = tl.rows[2]
        assertEquals("4565255703", entry.id)   // Toggl ids arrive as JSON numbers
        assertTrue(entry.logged)
        assertFalse(entry.canSwipeRight)      // already logged → inert on the right
        assertTrue(entry.canSwipeLeft)
        val running = tl.rows[3]
        assertTrue(running.canSwipeRight)
        assertEquals("done ✓ (20分)", running.rightLabel())
        assertEquals(listOf("-2"), running.tags)
        assertFalse(tl.rows[0].canSwipeRight)
        assertFalse(tl.rows[1].canSwipeLeft)   // gaps: right-swipe only
    }

    @Test
    fun timeline_points_null_when_neon_unreadable() {
        val tl = Parsers.timeline(JSONObject("""{"ok":true,"rows":[],"tracked_min":0,"points":null,"date":"2026-09-24"}"""))
        assertNull(tl.points)
    }

    @Test
    fun janus_bodies_match_web_payloads() {
        val tl = Parsers.timeline(JSONObject(timelineJson))
        val log = Bodies.log(tl.rows[3])
        assertEquals("4565255999", log.getString("id"))
        assertEquals(40, log.getInt("minutes"))
        assertEquals("-2", log.getJSONArray("tags").getString(0))
        val ev = Bodies.convertEvent(tl.rows[4])
        assertEquals("m5x2", ev.getString("code"))
        assertTrue(ev.getBoolean("is_past"))
        assertEquals("2026-09-24T12:30:00-07:00", ev.getString("start_iso"))
        assertEquals("bottom", Bodies.split("1", "bottom").getString("mode"))
        assertEquals("", Bodies.fill("nap", "13:00", "").getString("end"))
    }
}
