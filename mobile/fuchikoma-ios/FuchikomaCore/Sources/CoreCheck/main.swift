import Foundation
import FuchikomaCore

// Parsers and request bodies against literal payloads captured from the
// Flask services (tools/dtd/dtd.py, tools/janus/mobile.py). Same fixtures as
// the Android client's ModelsTest.kt. If a server field is renamed these
// fail before anything ships to the phone.

var failures = 0
func check(_ cond: @autoclosure () -> Bool, _ msg: String, file: String = #file, line: Int = #line) {
    if !cond() { failures += 1; print("FAIL \(line): \(msg)") }
}
func eq<T: Equatable>(_ a: T, _ b: T, _ msg: String = "", line: Int = #line) {
    if a != b { failures += 1; print("FAIL \(line): \(msg) — got \(a), expected \(b)") }
}

let tasksJson = """
{"ok": true, "tasks": [
  {"id": "6hcW1", "raw": "1 cal (15) [15]", "title": "1 cal", "est": 15, "points": 15,
   "color": "#7dd3fc", "domain": "hcb", "recurring": true, "variablePrompt": false},
  {"id": "6hcW2", "raw": "xk20 (30)", "title": "xk20", "est": 30, "points": null,
   "color": "#f9a8d4", "domain": "xk87", "recurring": false, "variablePrompt": true}
], "summary": {"points": 497, "done": 12}}
"""

// parses_tasks_with_nullable_points
do {
    let j = JSON.parse(tasksJson)!
    let tasks = Parsers.tasks(j)
    eq(tasks.count, 2, "task count")
    eq(tasks[0].points, 15, "points")
    check(tasks[1].points == nil, "null points → nil")
    check(tasks[1].variablePrompt, "variablePrompt")
    eq(tasks[0].color, "#7dd3fc", "color")
    eq(tasks[0].meta, "(15) [15]", "meta")
    eq(tasks[1].meta, "(30)", "meta without points")
    eq(Parsers.summary(j), DtdSummary(points: 497, done: 12), "summary")
}

// done_body_carries_typed_value_like_the_web
do {
    let t = Parsers.tasks(JSON.parse(tasksJson)!)[1]
    eq(Bodies.done(t, value: "45m")["content"] as? String, "xk20 45", "typed value rides in content")
    eq(Bodies.done(t, value: "")["content"] as? String, "xk20 (30)", "blank = skip → raw")
    eq(Bodies.done(t, value: nil)["content"] as? String, "xk20 (30)", "nil → raw")
    eq(Bodies.done(t)["id"] as? String, "6hcW2", "id")
}

// delay_options_shape
do {
    let d = Parsers.delayOptions(JSON.parse(#"{"ok":true,"blocks":[{"glyph":"酉","hour":16},{"glyph":"戌","hour":18}],"minutes":[10,30,60]}"#)!)
    eq(d.blocks.map(\.glyph), ["酉", "戌"], "block glyphs")
    eq(d.blocks.map(\.hour), [16, 18], "block hours")
    eq(d.minutes, [10, 30, 60], "minutes")
    eq(Bodies.delayHour(Parsers.tasks(JSON.parse(tasksJson)!)[0], hour: 16)["hour"] as? Int, 16, "delayHour body")
}

let timelineJson = """
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
"""

// parses_timeline_rows_and_numeric_ids
do {
    let tl = Parsers.timeline(JSON.parse(timelineJson)!)
    eq(tl.rows.count, 5, "row count")
    eq(tl.trackedMin, 512, "tracked")
    eq(tl.points, 497, "points")
    let entry = tl.rows[2]
    eq(entry.id, "4565255703", "Toggl ids arrive as JSON numbers → same digits")
    check(entry.logged, "logged")
    check(!entry.canSwipeRight, "already logged → inert on the right")
    check(entry.canSwipeLeft, "entry can edit")
    eq(entry.displayTitle, "✓ 0g", "logged prefix")
    let running = tl.rows[3]
    check(running.canSwipeRight, "running can swipe right")
    eq(running.rightLabel, "done ✓ (20分)", "running label")
    eq(running.tags, ["-2"], "tags")
    eq(running.displayTitle, "▶ vibing  #-2", "running title")
    eq(running.displayMeta, "14:52–now · 40m · 20分", "running meta")
    check(!tl.rows[0].canSwipeRight, "divider inert")
    check(!tl.rows[1].canSwipeLeft, "gaps: right-swipe only")
    eq(tl.rows[1].rightLabel, "fill", "gap label")
    eq(tl.rows[4].rightLabel, "log 分 (30m)", "past event label")
}

// timeline_points_null_when_neon_unreadable
do {
    let tl = Parsers.timeline(JSON.parse(#"{"ok":true,"rows":[],"tracked_min":0,"points":null,"date":"2026-09-24"}"#)!)
    check(tl.points == nil, "points null → nil")
}

// janus_bodies_match_web_payloads
do {
    let tl = Parsers.timeline(JSON.parse(timelineJson)!)
    let log = Bodies.log(tl.rows[3])
    eq(log["id"] as? String, "4565255999", "log id")
    eq(log["minutes"] as? Int, 40, "log minutes")
    eq((log["tags"] as? [String])?.first, "-2", "log tags")
    let ev = Bodies.convertEvent(tl.rows[4])
    eq(ev["code"] as? String, "m5x2", "event code")
    eq(ev["is_past"] as? Bool, true, "event is_past")
    eq(ev["start_iso"] as? String, "2026-09-24T12:30:00-07:00", "event start_iso")
    eq(Bodies.split(id: "1", mode: "bottom")["mode"] as? String, "bottom", "split mode")
    eq(Bodies.fill(desc: "nap", start: "13:00", end: "")["end"] as? String, "", "fill open end")
    for b in [log, ev, Bodies.done(Parsers.tasks(JSON.parse(tasksJson)!)[0])] {
        check(JSONSerialization.isValidJSONObject(b), "body serializes")
    }
}

// classify trusts the body's verdict over the HTTP status
do {
    if case .error(let m) = Api.classify(data: Data(#"{"ok":false,"error":"needs_agent"}"#.utf8), status: 200) {
        eq(m, "needs_agent", "ok:false with 200")
    } else { check(false, "expected error for ok:false") }
    if case .error(let m) = Api.classify(data: Data(#"{"detail":"boom"}"#.utf8), status: 500) {
        eq(m, "HTTP 500", "500 without ok")
    } else { check(false, "expected error for 500") }
    if case .error(let m) = Api.classify(data: Data("<html>bad gateway</html>".utf8), status: 502) {
        eq(m, "HTTP 502: not JSON", "non-JSON")
    } else { check(false, "expected error for html") }
    if case .ok(let j) = Api.classify(data: Data(#"{"ok":true,"closed":true,"already":false}"#.utf8), status: 200) {
        check(j.bool("closed"), "closed parsed")
        check(!j.bool("already"), "already false")
    } else { check(false, "expected ok") }
}

// config trims trailing slash and falls back to defaults
do {
    FuchikomaConfig.resetToDefaults()
    eq(FuchikomaConfig.dtdBase, FuchikomaConfig.defaultDtd, "default dtd")
    FuchikomaConfig.dtdBase = "https://ix.tail9c51d5.ts.net:5560/"
    eq(FuchikomaConfig.dtdBase, "https://ix.tail9c51d5.ts.net:5560", "trailing slash trimmed")
    FuchikomaConfig.dtdBase = "   "
    eq(FuchikomaConfig.dtdBase, FuchikomaConfig.defaultDtd, "blank → default")
    eq(FuchikomaConfig.dashboardURL, FuchikomaConfig.defaultDashboard, "dashboard keeps its trailing slash")
    FuchikomaConfig.resetToDefaults()
}

if failures == 0 { print("CoreCheck: all checks passed") } else { print("CoreCheck: \(failures) failure(s)"); exit(1) }
