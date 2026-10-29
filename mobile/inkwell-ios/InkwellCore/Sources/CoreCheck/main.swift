import Foundation
import InkwellCore

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
   "color": "#f9a8d4", "domain": "xk87", "recurring": false, "variablePrompt": true},
  {"id": "6hcW3", "raw": "ship inkwell (60) [40]", "title": "ship inkwell", "est": 60, "points": 40,
   "color": "#60a5fa", "domain": "i9", "recurring": false, "variablePrompt": false, "agent": true, "running": true}
], "summary": {"points": 497, "done": 12}}
"""

// parses_tasks_with_nullable_points
do {
    let j = JSON.parse(tasksJson)!
    let tasks = Parsers.tasks(j)
    eq(tasks.count, 3, "task count")
    check(!tasks[0].agent && !tasks[0].running, "flags default false when absent")
    check(tasks[2].agent && tasks[2].running, "agent + running parsed")
    eq(tasks[2].prefix, "▶ 😈 ", "prefix order: running, agent")
    eq(tasks[0].prefix, "↻ ", "recurring prefix")
    eq(tasks[0].points, 15, "points")
    check(tasks[1].points == nil, "null points → nil")
    check(tasks[1].variablePrompt, "variablePrompt")
    eq(tasks[0].color, "#7dd3fc", "color")
    eq(tasks[0].meta, "(15) [15]", "meta")
    eq(tasks[1].meta, "(30)", "meta without points")
    eq(Parsers.summary(j), DtdSummary(points: 497, done: 12), "summary")
}

// est_falls_back_to_raw_then_domain_default (bug 2026-10-22: hcmc/i444 blank)
do {
    let estJson = """
    {"ok": true, "tasks": [
      {"id": "a", "raw": "night hcmc", "title": "night hcmc", "est": null, "points": null,
       "color": "#0d3b66", "domain": "hcmc", "recurring": true, "variablePrompt": true},
      {"id": "b", "raw": "i444", "title": "i444", "est": null, "points": null,
       "color": "#616161", "domain": "i444", "recurring": true, "variablePrompt": true},
      {"id": "c", "raw": "read paper (45)", "title": "read paper", "est": null, "points": null,
       "color": "#0d3b66", "domain": "hcmc", "recurring": false, "variablePrompt": false},
      {"id": "d", "raw": "random (12)", "title": "random", "est": null, "points": null,
       "color": "#bdbdbd", "domain": "", "recurring": false, "variablePrompt": false}
    ]}
    """
    let ts = Parsers.tasks(JSON.parse(estJson)!)
    eq(ts[0].est, 30, "hcmc default est")
    eq(ts[0].meta, "(30)", "hcmc shows default time")
    eq(ts[1].est, 15, "i444 default est")
    eq(ts[2].est, 45, "raw (N) wins over domain default")
    eq(ts[3].est, 12, "raw (N) recovers est for domains without a default")
}

// variable_prompt_is_the_server_label (2026-10-25: cpap never asked). dtd.py
// sends the prompt TEXT or null, not a Bool; ritual cards now carry 分.
do {
    let vj = """
    {"ok": true, "tasks": [
      {"id": "c", "raw": "cpap (1) [3]", "title": "cpap", "est": "(1) [3]", "points": 3,
       "domain": "hcb", "variablePrompt": "CPAP quality (1-3)"},
      {"id": "x", "raw": "xk22 (20)", "title": "xk22", "points": 0, "variablePrompt": "xk22 minutes (Ren)"},
      {"id": "p", "raw": "plain [5]", "title": "plain", "points": 5, "variablePrompt": null},
      {"id": "r", "raw": "😈 -1ibx", "title": "😈 -1ibx", "est": "[3]", "points": 3, "variablePrompt": null}
    ]}
    """
    let ts = Parsers.tasks(JSON.parse(vj)!)
    check(ts[0].variablePrompt && ts[1].variablePrompt, "label string → prompts")
    eq(ts[0].variableLabel, "CPAP quality (1-3)", "label kept for the alert")
    check(!ts[2].variablePrompt && ts[2].variableLabel.isEmpty, "null → no prompt")
    eq(ts[3].meta, "[3]", "ritual card shows its points")
    let f = Parsers.form(JSON.parse(#"{"ok":true,"form":"0s","title":"0s","fields":[],"keepsCard":false,"closesIf":{"points_checked":"1"}}"#.data(using: .utf8)!)!)!
    check(f.closesCard(["points_checked": "1"]) && !f.closesCard(["points_checked": "0"]), "0s closes only when checked")
    let k = Parsers.form(JSON.parse(#"{"ok":true,"form":"1s","title":"1s","fields":[],"keepsCard":true,"closesIf":{}}"#.data(using: .utf8)!)!)!
    check(!k.closesCard([:]), "1s keeps its card")
    let old = Parsers.form(JSON.parse(#"{"ok":true,"form":"1hcb","title":"1 hcb","fields":[]}"#.data(using: .utf8)!)!)!
    check(old.closesCard([:]), "no hint (older server) → closes")
}

// hides_tasks_deferred_to_later_today (bug 2026-10-22: later-today rows showed now)
do {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = .current
    f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    let now = f.date(from: "2026-10-22T12:00:00")!
    let schedJson = """
    {"ok": true, "tasks": [
      {"id": "late", "raw": "call bob (15)", "title": "call bob", "domain": "i9", "scheduled": "2026-10-22T15:00:00"},
      {"id": "early", "raw": "standup (10)", "title": "standup", "domain": "i9", "scheduled": "2026-10-22T09:00:00"},
      {"id": "dateonly", "raw": "pay rent", "title": "pay rent", "domain": "qz12", "scheduled": "2026-10-22"},
      {"id": "none", "raw": "inbox", "title": "inbox", "domain": "i447"},
      {"id": "tomorrow", "raw": "flight (30)", "title": "flight", "domain": "家", "scheduled": "2026-10-23T08:00:00"}
    ]}
    """
    let ts = Parsers.tasks(JSON.parse(schedJson)!)
    eq(ts.count, 5, "parsed all rows")
    check(ts[0].scheduledTime != nil, "later-today datetime parsed")
    check(ts[2].scheduledTime == nil, "date-only scheduled → nil (no time-of-day)")
    check(ts[3].scheduledTime == nil, "absent scheduled → nil")
    check(ts[0].deferredToLaterToday(now), "15:00 is later today → deferred")
    check(!ts[1].deferredToLaterToday(now), "09:00 already passed → visible")
    check(!ts[2].deferredToLaterToday(now), "date-only → never later-today")
    check(!ts[4].deferredToLaterToday(now), "tomorrow is a different day → not later-TODAY")
    eq(DtdTask.visible(ts, now: now).map(\.id), ["early", "dateonly", "none", "tomorrow"],
       "only the later-today row is hidden")
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
    eq(running.rightLabel, "done (stop + log)", "running label: no 分 estimate")
    eq(running.tags, ["-2"], "tags")
    eq(running.displayTitle, "▶ vibing  #-2", "running title")
    eq(running.displayMeta, "14:52", "running meta: start only")
    eq(running.rightMeta, "40m · [20]", "running right meta")
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

// dtd_fuzzy_word: subsequence match, contiguous short-circuit, AND-per-word
do {
    func fuzzy(_ w: String, _ hay: String) -> Bool {   // mirror of DtdStore.fuzzyWord
        if hay.contains(w) { return true }
        var it = hay.makeIterator()
        for ch in w { var hit = false; while let h = it.next() { if h == ch { hit = true; break } }; if !hit { return false } }
        return true
    }
    check(fuzzy("biks", "fix bikes (delegate?) xk87"), "dropped letter still matches (subsequence)")
    check(fuzzy("xk87", "kids nature xk87"), "bare domain code matches contiguously")
    check(!fuzzy("prius", "kids nature lands end"), "no 'prius' chars in order → no match")
}

// timeline_is_today: present → honoured; absent → defaults true (old cache)
do {
    let past = Parsers.timeline(JSON.parse(#"{"ok":true,"rows":[],"tracked_min":0,"points":null,"date":"2026-10-21","is_today":false}"#)!)
    check(!past.isToday, "is_today:false → read-only past view")
    let today = Parsers.timeline(JSON.parse(#"{"ok":true,"rows":[],"tracked_min":0,"points":null,"date":"2026-10-22","is_today":true}"#)!)
    check(today.isToday, "is_today:true → live")
    check(Parsers.timeline(JSON.parse(timelineJson)!).isToday, "is_today absent → defaults true")
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
    check(Bodies.fill(desc: "nap", start: "13:00", end: "")["project"] == nil, "fill omits empty project")
    eq(Bodies.fill(desc: "nap", start: "13:00", end: "", project: "hcb")["project"] as? String, "hcb", "fill carries project")
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
    InkwellConfig.resetToDefaults()
    eq(InkwellConfig.dtdBase, InkwellConfig.defaultDtd, "default dtd")
    InkwellConfig.dtdBase = "https://ix.tail9c51d5.ts.net:8560/"
    eq(InkwellConfig.dtdBase, "https://ix.tail9c51d5.ts.net:8560", "trailing slash trimmed")
    InkwellConfig.dtdBase = "   "
    eq(InkwellConfig.dtdBase, InkwellConfig.defaultDtd, "blank → default")
    eq(InkwellConfig.dashboardURL, InkwellConfig.defaultDashboard, "dashboard keeps its trailing slash")
    InkwellConfig.resetToDefaults()
}

// markdown list autofill continues / breaks out like Obsidian
do {
    // Insert at caret (end of line): continue the marker on the next line.
    func cont(_ s: String) -> MarkdownList.Edit? {
        MarkdownList.onNewline(text: s as NSString, selection: NSRange(location: (s as NSString).length, length: 0))
    }
    eq(cont("- milk")?.replacement, "\n- ", "unordered continues")
    eq(cont("* milk")?.replacement, "\n* ", "asterisk bullet preserved")
    eq(cont("+ milk")?.replacement, "\n+ ", "plus bullet preserved")
    eq(cont("1. first")?.replacement, "\n2. ", "ordered increments")
    eq(cont("9. nine")?.replacement, "\n10. ", "ordered rolls into two digits")
    eq(cont("3) third")?.replacement, "\n4) ", "paren delimiter preserved")
    eq(cont("- [ ] todo")?.replacement, "\n- [ ] ", "checkbox continues unchecked")
    eq(cont("- [x] done")?.replacement, "\n- [ ] ", "checked box continues unchecked")
    eq(cont("  - nested")?.replacement, "\n  - ", "indent preserved")
    eq(cont("\t1. tabbed")?.replacement, "\n\t2. ", "tab indent preserved")
    check(cont("plain prose") == nil, "non-list line → plain newline")
    check(cont("") == nil, "empty buffer → plain newline")

    // Empty item: break out — strip the marker, no new line.
    let e1 = cont("- ")
    eq(e1?.replacement, "", "empty bullet strips marker")
    eq(e1?.range, NSRange(location: 0, length: 2), "clears the whole '- ' marker")
    eq(e1?.cursor, 0, "caret returns to line start")
    eq(cont("  - ")?.range, NSRange(location: 0, length: 4), "empty nested bullet clears indent+marker")
    eq(cont("1. ")?.replacement, "", "empty ordered breaks out")
    eq(cont("- [ ] ")?.replacement, "", "empty checkbox breaks out")

    // Caret position carries through on a continuation.
    let mid = "- a\n- b"
    let e2 = MarkdownList.onNewline(text: mid as NSString, selection: NSRange(location: 3, length: 0))
    eq(e2?.replacement, "\n- ", "continuation mid-buffer")
    eq(e2?.cursor, 6, "caret sits after the inserted marker")

    // A non-empty selection never triggers autofill.
    check(MarkdownList.onNewline(text: "- a" as NSString, selection: NSRange(location: 0, length: 3)) == nil,
          "selection replace → plain newline")
}

// headings and pipe tables (Inkwell renderer + live editor styling)
do {
    eq(MarkdownLine.headingLevel("# We are XBOX"), 1, "h1")
    eq(MarkdownLine.headingLevel("### Further details"), 3, "h3")
    eq(MarkdownLine.headingLevel("  ## indented"), 2, "leading spaces allowed")
    eq(MarkdownLine.headingLevel("##"), 2, "empty heading still a heading")
    check(MarkdownLine.headingLevel("#tag in prose") == nil, "#tag is not a heading")
    check(MarkdownLine.headingLevel("####### seven") == nil, "seven hashes is not a heading")
    check(MarkdownLine.headingLevel("plain") == nil, "plain line")

    check(MarkdownLine.isTableRow("| a | b |"), "piped row")
    check(MarkdownLine.isTableRow("|a|b"), "no trailing pipe still a row")
    check(!MarkdownLine.isTableRow("a | b"), "prose with a pipe is not a table")
    check(!MarkdownLine.isTableRow("|"), "lone pipe is not a row")
    check(MarkdownLine.isTableSeparator("|---|:--:| --: |"), "separator row")
    check(!MarkdownLine.isTableSeparator("| a | --- |"), "content row is not a separator")
    eq(MarkdownLine.tableCells("| a | **b** |  |"), ["a", "**b**", ""], "cells trimmed, empty cell kept")
    eq(MarkdownLine.tableCells(#"| x \| y | z |"#), ["x | y", "z"], "escaped pipe stays in the cell")
    eq(MarkdownLine.alignments("|:--|:-:|--:|---|"), [.leading, .center, .trailing, .leading], "alignments")
}

// Round 3 (2026-10-22): survey forms, janus preview points, offline outbox.
do {
    let f = Parsers.form(JSON.parse(#"{"ok":true,"form":"0s","title":"daily review","fields":[{"key":"title","label":"title","type":"text"},{"key":"mood","label":"mood","type":"scale","min":1,"max":7,"value":"5"}]}"#.data(using: .utf8)!)!)
    eq(f?.name, "0s", "form name")
    eq(f?.fields.count, 2, "form fields")
    eq(f?.fields[1].max, 7, "scale max")
    eq(f?.fields[1].value, "5", "prefill value")
    check(Parsers.form(JSON.parse(#"{"ok":false,"form":null}"#.data(using: .utf8)!)!) == nil, "no form → nil")

    let tl = Parsers.timeline(JSON.parse(#"{"date":"2026-10-22","rows":[{"type":"entry","id":"1","start":"15:05","end":"15:30","minutes":25,"desc":"XTECH Huddle","logged":false,"points":null,"preview_points":30}]}"#.data(using: .utf8)!)!)
    let r = tl.rows[0]
    eq(r.previewPoints, 30, "preview points parsed")
    eq(r.rightLabel, "log 分", "swipe label carries no 分 estimate (2026-10-25)")
    eq(r.displayMeta, "15:05", "start time only")
    eq(r.rightMeta, "25m", "no ~preview 分 in the right column (2026-10-25)")
    var pend = TimelineRow(type: "entry"); pend.id = "pending-x"
    check(pend.isPending && !pend.isLocal && !r.isPending, "optimistic start row is pending, not local")

    check(Outbox.isOffline("offline") && Outbox.isOffline("unreachable (tailnet down?)"), "offline errors queue")
    check(!Outbox.isOffline("timed out") && !Outbox.isOffline("HTTP 500"), "timeouts/server errors don't queue")
    let ob = Outbox("corecheck"); ob.clear()
    ob.enqueue(url: "http://x/api/done", body: ["id": "1"], label: "done a")
    ob.enqueue(url: "http://x/api/done", body: ["id": "2"], label: "done b")
    eq(ob.items.map(\.label), ["done a", "done b"], "outbox keeps order")
    ob.clear()
}

// Offline → online sync (2026-10-22 /bug): a stubbed network that first
// refuses every request (no connection), then accepts and records them.
final class StubNet: URLProtocol {
    static var online = false
    static var seen: [(path: String, body: [String: Any])] = []
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard StubNet.online else {
            client?.urlProtocol(self, didFailWithError: NSError(domain: NSURLErrorDomain, code: NSURLErrorNotConnectedToInternet))
            return
        }
        var data = request.httpBody
        if data == nil, let s = request.httpBodyStream {
            s.open(); var d = Data(); var buf = [UInt8](repeating: 0, count: 4096)
            while s.hasBytesAvailable { let n = s.read(&buf, maxLength: buf.count); if n <= 0 { break }; d.append(buf, count: n) }
            s.close(); data = d
        }
        let body = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        StubNet.seen.append((request.url?.path ?? "", body))
        let resp = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
        let reply = request.url?.path == "/api/add" ? #"{"ok":true,"id":"NEW9"}"#
            : (body["desc"] as? String) == "ask-me" ? #"{"ok":true,"needs_points":true,"desc":"ask-me","minutes":30}"#
            : #"{"ok":true}"#
        client?.urlProtocol(self, didLoad: reply.data(using: .utf8)!)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

do {
    let cfg = URLSessionConfiguration.ephemeral
    cfg.protocolClasses = [StubNet.self]
    let api = Api(configuration: cfg)
    let ob = Outbox("corecheck-sync"); ob.clear()
    // What the dtd app queues offline: an add, a done on an existing task,
    // and a done on the task just added (content-only, no Todoist id yet).
    ob.enqueue(url: "http://ix/api/add", body: ["content": "buy milk (5) [2]"], label: "add", ref: "add:local-1")
    ob.enqueue(url: "http://ix/api/done", body: ["id": "T1", "content": "hiit (20) [10]"], label: "done T1", ref: "hide:T1")
    ob.enqueue(url: "http://ix/api/done", body: ["id": "$ref:add:local-1", "content": "buy milk (5) [2]"],
               label: "done local", ref: "hide:local-1")

    let sema = DispatchSemaphore(value: 0)
    var offlineRun: (sent: Int, rejected: [String], refs: [String]) = (0, [], [])
    var onlineRun: (sent: Int, rejected: [String], refs: [String]) = (0, [], [])
    var seenOffline = -1
    var queuedOffline = -1
    Task {
        StubNet.online = false
        offlineRun = await ob.flush(api)
        seenOffline = StubNet.seen.count; queuedOffline = ob.count
        StubNet.online = true
        onlineRun = await ob.flush(api)
        sema.signal()
    }
    sema.wait()
    eq(offlineRun.sent, 0, "offline flush sends nothing")
    eq(seenOffline, 0, "offline: no request reached the server")
    eq(queuedOffline, 3, "offline: all three stay queued")
    eq(onlineRun.sent, 3, "online flush sends all three")
    eq(ob.count, 0, "queue empty after sync")
    eq(StubNet.seen.map(\.path), ["/api/add", "/api/done", "/api/done"], "replayed in order")
    eq(StubNet.seen[1].body["id"] as? String, "T1", "offline done keeps its task id")
    eq(StubNet.seen[2].body["id"] as? String, "NEW9", "local task's done gets the id its add returned")
    eq(onlineRun.refs, ["add:local-1", "hide:T1", "hide:local-1"], "refs returned so the UI retires local rows")
    ob.clear()
}

// janus logged rows show 分 credited; log toast lists where it went (2026-10-26)
do {
    let tl = Parsers.timeline(JSON.parse(#"{"date":"2026-10-26","rows":[{"type":"entry","id":"1","start":"09:00","end":"09:40","minutes":40,"desc":"gym","logged":true,"points":null,"logged_points":17},{"type":"entry","id":"2","start":"09:40","end":"10:00","minutes":20,"desc":"x","logged":true,"points":null},{"type":"entry","id":"3","start":"10:00","end":"10:30","minutes":30,"desc":"y","logged":false,"points":25}]}"#)!)
    eq(tl.rows[0].loggedPoints, 17, "logged_points parsed")
    eq(tl.rows[0].rightMeta, "[17]", "logged row: 分 instead of duration")
    eq(tl.rows[1].rightMeta, "20m", "logged without a number keeps duration")
    eq(tl.rows[2].rightMeta, "30m · [25]", "unlogged row keeps duration")
    eq(Parsers.creditsSummary(JSON.parse(#"{"ok":true,"credits":[{"col":"hcb","points":12},{"col":"0g","points":5}]}"#)!),
       "+12 hcb · +5 0g", "credits toast text")
    check(Parsers.creditsSummary(JSON.parse(#"{"ok":true,"credits":[]}"#)!) == nil, "no credits → nil")
    check(Parsers.creditsSummary(JSON.parse(#"{"ok":true}"#)!) == nil, "old server → nil")
}

// A replayed log the server answers needs_points (lib/janus_done.py "ask")
// credited nothing: it must be reported, not counted as sent (v172 review).
do {
    let cfg = URLSessionConfiguration.ephemeral
    cfg.protocolClasses = [StubNet.self]
    let api = Api(configuration: cfg)
    let ob = Outbox("corecheck-needs-points"); ob.clear()
    StubNet.seen = []
    ob.enqueue(url: "http://ix/api/log", body: ["id": "E1", "desc": "ask-me", "minutes": 30], label: "log ask-me")
    ob.enqueue(url: "http://ix/api/log", body: ["id": "E2", "desc": "gym", "minutes": 40], label: "log gym")
    let sema = DispatchSemaphore(value: 0)
    var run: (sent: Int, rejected: [String], refs: [String]) = (0, [], [])
    Task { StubNet.online = true; run = await ob.flush(api); sema.signal() }
    sema.wait()
    eq(run.sent, 1, "needs_points reply is not counted as sent")
    eq(run.rejected, ["log ask-me: needs points — swipe it again"], "needs_points reply is reported")
    eq(ob.count, 0, "queue still drains past it")
    ob.clear()
}

if failures == 0 { print("CoreCheck: all checks passed") } else { print("CoreCheck: \(failures) failure(s)"); exit(1) }
