import Foundation
import InkwellCore

/// janus: today's timeline (Toggl entries, gaps, calendar events, 地支
/// dividers). Leading swipe: entry → log 分, running → stop+log, event →
/// convert to a Toggl entry, gap → fill dialog. Trailing swipe on an entry →
/// edit/split · delete. Every action reloads the whole timeline (no optimism
/// on 分 — the server owns the ledger; a started timer is shown at once).
@MainActor
final class JanusStore: ObservableObject {
    @Published private(set) var rows: [TimelineRow] = []
    @Published private(set) var timeline: Timeline? = nil
    @Published private(set) var lastError: String? = nil
    @Published private(set) var loadedOnce = false
    @Published private(set) var loading = false
    @Published private(set) var busy = false   // one action at a time; did-fast writes aren't idempotent
    /// 0 = today, -1 = yesterday, … (≤0). The back/forward day view (the CLI's
    /// ctrl+/ / ctrl+=). Rows on a past day swipe too (2026-10-26): each write
    /// carries the viewed date (see `dated`); the bottom bar (timer + command
    /// bar) still hides there, since both act on today.
    @Published private(set) var dayOffset = 0
    /// Bumped whenever the list should snap back to its default position, the
    /// last completed entry (2026-10-26: on open / day switch / refresh, and
    /// after a time is set). The view scrolls on change; the passive poll
    /// never bumps it, so it can't yank the list out from under a scroll.
    @Published private(set) var scrollTick = 0
    private var wantsDefaultScroll = true

    private let api = Api()
    private let outbox = Outbox("janus")
    /// Writes queued while offline (⇡N in the header), replayed on reconnect.
    @Published private(set) var pending = 0
    /// The server's needs_points answer (lib/janus_done.py, same rule as the
    /// CLI): neither a task with [N] nor a time-based habit/category, so the
    /// view asks for a total ("40") or a rate (".5/m") and re-sends.
    @Published var pointsAsk: PointsAsk? = nil
    private var loadedAt: Date? = nil
    private var cachedAt: Date? = nil
    private var loadSeq = 0
    private var base: String { InkwellConfig.janusBase }
    private let toast = Toaster.shared

    /// How long a good load stays "fresh" before a foreground-return or the
    /// on-screen poll will refetch. Deliberately short (2026-10-22: a coarse
    /// gate let the timeline sit visibly stale — entries logged on desktop or
    /// another device didn't appear for ages). Writes still invalidate at once
    /// via `act` → `load`; this only bounds *passive* staleness.
    static let cacheTTL: TimeInterval = 60

    init() {
        // Only today's cached timeline is worth painting (2026-10-20 "should be
        // today": yesterday's rows showed until the fetch landed).
        if let (j, at) = PayloadCache.load("janus") {
            let t = Parsers.timeline(j)
            if t.date == PayloadCache.todayISO() { timeline = t; rows = overlay(t.rows); cachedAt = at }
        }
        pending = outbox.count
    }

    /// The live Toggl entry, if any (the top card + what the command bar acts on).
    var running: TimelineRow? { rows.first { $0.running } }

    /// Today vs a past day (no bottom bar there). Keyed off dayOffset (authoritative on the
    /// client) so the view gates immediately on a tap, before the fetch lands.
    var viewingToday: Bool { dayOffset == 0 }

    /// "◀ Wed 10/21" while viewing a past day; "" on today.
    var dayLabel: String {
        guard dayOffset != 0, let d = timeline?.date else { return "" }
        let inF = DateFormatter(); inF.dateFormat = "yyyy-MM-dd"; inF.locale = Locale(identifier: "en_US_POSIX")
        guard let date = inF.date(from: d) else { return d }
        let outF = DateFormatter(); outF.dateFormat = "EEE M/d"
        return "◀ " + outF.string(from: date)
    }

    func prevDay() { dayOffset -= 1; wantsDefaultScroll = true; Task { await load() } }
    func nextDay() { guard dayOffset < 0 else { return }; dayOffset += 1; wantsDefaultScroll = true; Task { await load() } }
    func goToday() { guard dayOffset != 0 else { return }; dayOffset = 0; wantsDefaultScroll = true; Task { await load() } }

    /// The default list position: the most recent completed entry (not the
    /// running timer, not a gap); else the last row. Index into `rows`.
    var defaultScrollIndex: Int? {
        rows.lastIndex { $0.isEntry && !$0.running && !$0.isPending } ?? (rows.isEmpty ? nil : rows.count - 1)
    }
    /// Snap to the default position on the next published rows.
    func requestDefaultScroll() { wantsDefaultScroll = true }
    private func publishScrollIfWanted() { if wantsDefaultScroll { scrollTick += 1 } }

    /// A write against a past-day row names that day (2026-10-26: "should be
    /// able to swipe on previous days tasks"); the server logs it with the
    /// day's M/D + --past-ok and looks the Toggl entry up around that date.
    /// Today's writes stay date-less (server default = today).
    func dated(_ b: [String: Any]) -> [String: Any] {
        guard let t = timeline, !t.isToday, !t.date.isEmpty else { return b }
        var out = b; out["date"] = t.date; return out
    }

    /// The ISO date a given offset should resolve to, computed client-side so
    /// load() can tell a cold/day-switched view (wants the fast lite paint)
    /// from a warm reload after an action (full fetch only, no flicker).
    private func expectedDate(_ offset: Int) -> String {
        let d = Calendar.current.date(byAdding: .day, value: offset, to: Date()) ?? Date()
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX")
        return f.string(from: d)
    }

    var title: String {
        let pts = timeline?.points.map { "\($0)分" } ?? "–分"
        let tracked = timeline.map { String(format: "%d:%02d", $0.trackedMin / 60, $0.trackedMin % 60) } ?? "0:00"
        return "\(pts) · janus · \(tracked) tracked" + (pending > 0 ? " · ⇡\(pending)" : "")
    }
    var status: String {
        if let e = lastError { return "⚠ " + e }
        if !loadedOnce, let at = cachedAt { return "cached \(PayloadCache.clock(at)) · refreshing…" }
        return ""
    }

    func reload() { wantsDefaultScroll = true; Task { await load() } }
    /// Same rule as DtdStore: a failed or never-done load retries at once on
    /// foreground; a good one only when stale.
    func refreshIfStale() {
        if lastError != nil || !loadedOnce { Task { await load() }; return }
        guard let at = loadedAt, Date().timeIntervalSince(at) > Self.cacheTTL else { return }
        Task { await load() }
    }

    /// Replay offline writes, then reload if any landed. Called on foreground,
    /// on network return (RootView's path monitor) and before every load.
    func flushOutbox() async {
        // The timer still running from offline goes last: a live Toggl timer
        // started at its real (offline) start time.
        if NetWatch.shared.online, let i = localTimers.firstIndex(where: { $0.end == nil && !$0.queued }) {
            let lt = localTimers[i]
            outbox.enqueue(url: base + "/api/fill", body: Bodies.fill(desc: lt.desc, start: Self.hhmm(lt.start), end: "",
                                                                     project: lt.project), label: "start \(lt.desc)")
            localTimers[i].queued = true
        }
        guard outbox.count > 0 else { return }
        let r = await outbox.flush(api)
        pending = outbox.count
        if pending == 0 { localTimers = []; stoppedServer = [:] }
        if !r.rejected.isEmpty { toast.show("sync refused: " + r.rejected.joined(separator: "; ")) }
        else if r.sent > 0 { toast.show("synced \(r.sent) offline change\(r.sent == 1 ? "" : "s") ✓") }
    }

    func load() async {
        await flushOutbox()
        loading = true
        loadSeq += 1
        let seq = loadSeq
        let offsetQ = dayOffset != 0 ? "offset=\(dayOffset)" : ""
        // Two-phase first paint (mirrors the web, 2026-10-22): on a cold load
        // or a day switch, a lite pass (Toggl entries + gaps only — no calendar
        // subprocesses, no Neon reads, ~0.3 s vs ~15 s) paints fast, then the
        // full payload replaces it. A warm reload after an action skips lite so
        // calendar events / points don't flicker out and back. A newer load()
        // (another day tap) bumps loadSeq and supersedes this one's writes.
        let fresh = timeline?.date != expectedDate(dayOffset)
        if fresh {
            let liteQ = ["lite=1", offsetQ].filter { !$0.isEmpty }.joined(separator: "&")
            if case .ok(let j) = await api.get(base + "/api/timeline?" + liteQ) {
                if seq == loadSeq {
                    let t = Parsers.timeline(j); timeline = t; rows = overlay(t.rows)
                    publishScrollIfWanted()
                }
            }
        }
        let fullURL = base + "/api/timeline" + (offsetQ.isEmpty ? "" : "?" + offsetQ)
        switch await api.get(fullURL) {
        case .ok(let j):
            guard seq == loadSeq else { loading = false; return }
            loadedOnce = true; lastError = nil; loadedAt = Date()
            let t = Parsers.timeline(j)
            timeline = t; rows = overlay(t.rows)
            publishScrollIfWanted(); wantsDefaultScroll = false
            // Only today's payload is restored on next launch (see init); never
            // cache a past-day view under the "janus" key.
            if t.isToday { PayloadCache.save("janus", j) }
        case .error(let m):
            if seq == loadSeq { lastError = m }
        }
        loading = false
    }

    // ── leading-swipe commits ────────────────────────────────────────────
    func onRight(_ r: TimelineRow) {
        if r.isPending { toast.show("starting… one sec"); return }
        if r.isEvent {
            act("/api/convert-event", dated(Bodies.convertEvent(r))) { d in
                d.string("mode") == "logged"
                    ? Self.loggedToast(d, fallback: "+\(r.minutes)m → \(d.string("step", "neon"))")
                    : "tracking started ✓" }
        } else if r.running && (r.isLocal || !NetWatch.shared.online) {
            stopOffline(r)
        } else if r.running {
            act("/api/done-current", Bodies.doneCurrent(r)) { d in
                d.bool("credited") ? "stopped · already crossed off ✓"
                    : "stopped + " + Self.loggedToast(d, fallback: "logged → \(d.string("step", "neon"))") }
        } else if r.isLocal {
            // Its Toggl entry doesn't exist yet; logging now and again after
            // sync would credit twice.
            toast.show("offline entry · log it once it syncs")
        } else if r.logged {
            // Swipe a logged row again = "these points were wrong" (desktop
            // janus ^G, JM 2026-10-28): ask for the right total, the server
            // appends the difference in the entry's own block.
            pointsAsk = PointsAsk(path: "/api/adjust-points",
                                  body: dated(["id": r.id, "desc": r.desc, "project": r.project]),
                                  desc: r.desc, minutes: r.minutes,
                                  current: r.loggedPoints ?? r.points) { d in
                d.bool("unchanged") ? "points unchanged"
                    : "points → \(d.int("points")) (\(d.int("delta") >= 0 ? "+" : "")\(d.int("delta"))) ✓" }
        } else {
            act("/api/log", dated(Bodies.log(r)), offline: "log \(r.desc)", optimistic: { [weak self] in
                self?.markLogged(r) }) { d in
                if d.bool("credited") { return "already crossed off — no extra 分" }
                if d.bool("already") { return "already logged" }
                let extra = d.strings("tag_steps").joined(separator: ", ")
                return Self.loggedToast(d, fallback: "+\(r.minutes)m → \(d.string("step", "neon"))"
                                        + (extra.isEmpty ? "" : " + \(extra)"))
            }
        }
    }

    /// Re-send the asked-for write with the typed answer ("40" or ".5/m").
    func answerPoints(_ a: PointsAsk, _ text: String) {
        var body = a.body
        body["points"] = text.trimmingCharacters(in: .whitespaces)
        act(a.path, body, toastText: a.toastText)
    }

    /// "logged: +12 hcb · +5 0g ✓" from the server's per-column credits
    /// (2026-10-26: "a toast saying what points went where"); an older
    /// server without `credits` gets the step-based fallback.
    static func loggedToast(_ d: JSON, fallback: String) -> String {
        (Parsers.creditsSummary(d).map { "logged: " + $0 } ?? fallback) + " ✓"
    }

    /// One POST, then a full reload. `needs_agent` is the server saying it
    /// has no route for this description — the web sends you to desktop /did.
    /// `offline`: a label → this write may be queued when the phone has no
    /// route to Ix (only replay-safe writes pass one), and `optimistic` then
    /// applies its effect to the visible rows. Haptics: a light tick when the
    /// swipe fires, a success buzz once the server has registered it
    /// (round 3 #2: "give haptic feedback when registered").
    /// `onOk` runs on success *before* the reload, for effects the user should
    /// see at once (2026-10-25: a just-started timer fills the timer card
    /// immediately instead of after the up-to-15 s full timeline fetch).
    func act(_ path: String, _ body: [String: Any], offline: String? = nil, queuedBody: [String: Any]? = nil,
             optimistic: (() -> Void)? = nil, onOk: ((JSON) -> Void)? = nil,
             toastText: @escaping (JSON) -> String) {
        guard !busy else { return }
        busy = true
        Platform.haptic(.commit)
        Task {
            let r: ApiResult = NetWatch.shared.online
                ? await Platform.keepAlive { await api.post(base + path, body) }
                : .error("offline")   // known offline: queue now, don't wait on a timeout
            busy = false
            switch r {
            case .ok(let j) where j.bool("needs_points"):
                Platform.haptic(.commit)
                pointsAsk = PointsAsk(path: path, body: body, desc: j.string("desc"),
                                      minutes: j.int("minutes"), current: nil, toastText: toastText)
            case .ok(let j): Platform.haptic(.success); toast.show(toastText(j)); onOk?(j); await load()
            case .error(let m) where offline != nil && Outbox.isOffline(m):
                outbox.enqueue(url: base + path, body: queuedBody ?? body, label: offline!)
                pending = outbox.count
                optimistic?()
                Platform.haptic(.success)
                toast.show("offline · queued ⇡\(pending), syncs when back online")
            case .error(let m):
                Platform.haptic(.failure)
                toast.show(m.contains("needs_agent") ? "no route — use /did on desktop" : m)
            }
        }
    }

    /// Offline log: show the row as ✓ until the queued write lands.
    private func markLogged(_ r: TimelineRow) {
        rows = rows.map { x in
            guard x.isEntry, x.id == r.id else { return x }
            var y = x; y.logged = true; return y
        }
    }

    /// The CLI's typed-command path (web command bar): "coffee @epcn",
    /// "1400 coffee", "0000-0550 睡觉", "+5", "/d <task>". Server routes each
    /// comma-separated part to tg-fast or did-fast.
    /// A typed command with no time of its own ("coffee @epcn") means "start
    /// now". Pin "now" into it ("1842 coffee @epcn", the CLI's own HHMM-start
    /// form) so a copy replayed from the offline queue later still starts at
    /// the moment it was typed. Ranges, HHMM starts, +N, /d and multi-part
    /// commands pass through untouched.
    static func pinStart(_ t: String, now: Date) -> String {
        let first = t.split(separator: " ").first.map(String.init) ?? ""
        if t.hasPrefix("/") || t.hasPrefix("+") || t.contains(",") || t.contains("，") { return t }
        if first.count == 4, Int(first) != nil { return t }
        if t.range(of: #"\b\d{4}-\d{4}\b"#, options: .regularExpression) != nil { return t }
        if trailingStart(t) != nil { return t }   // "desc 1626-" carries its own start
        let f = DateFormatter(); f.dateFormat = "HHmm"
        return f.string(from: now) + " " + t
    }

    func run(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        // Offline, a plain "start something" command becomes a local timer.
        if !NetWatch.shared.online, let (desc, at) = Self.parseStart(t, now: Date()) {
            startOffline(desc: desc, project: "", at: at); return
        }
        let started = Self.optimisticStart(t, now: Date())
        requestDefaultScroll()   // a new time resets the list (2026-10-26)
        act("/api/run", ["text": t], offline: t, queuedBody: ["text": Self.pinStart(t, now: Date())],
            onOk: { [weak self] _ in
                if let (desc, project, at) = started { self?.showStarted(desc: desc, project: project, at: at) }
            }) { d in d.string("msg", "ok ✓") }
    }

    func fill(desc: String, start: String, end: String, project: String = "") {
        if !NetWatch.shared.online && end.isEmpty {
            startOffline(desc: desc, project: project, at: Self.todayAt(start) ?? Date()); return
        }
        // A closed range is replay-safe; an ongoing start-now is not (its
        // start is fixed in the body, but a duplicate would start twice).
        requestDefaultScroll()
        act("/api/fill", dated(Bodies.fill(desc: desc, start: start, end: end, project: project)),
            offline: end.isEmpty ? nil : "fill \(desc)",
            onOk: { [weak self] d in
                guard end.isEmpty, d.bool("running") else { return }
                let p = d.string("project")
                self?.showStarted(desc: Self.stripAt(desc).0, project: p.isEmpty ? project : p,
                                  at: Self.todayAt(start) ?? Date())
            }) { d in
            d.bool("running") ? "▶ started" + (d.string("project").isEmpty ? "" : " → " + d.string("project")) + " ✓" : "saved ✓" }
    }
    func edit(_ r: TimelineRow, desc: String, start: String, end: String, project: String) {
        if start != r.start || end != (r.running ? "" : r.end) { requestDefaultScroll() }
        act("/api/edit", dated(Bodies.edit(id: r.id, desc: desc, start: start, end: end, project: project)),
            offline: "edit \(desc)") { _ in "edited ✓" }
    }
    /// Resume an entry (janus full left-swipe): start a fresh ongoing Toggl
    /// timer carrying the same description + project. There is no dedicated
    /// server route for resuming an *entry* — only calendar events resume via
    /// /api/convert-event — so this reuses /api/fill with a blank end, which
    /// is exactly what "start now" does and which the server returns as a
    /// running entry. Tags aren't carried (fill has no tags field), matching
    /// the existing fill / start-now behaviour.
    func resume(_ r: TimelineRow) {
        if !NetWatch.shared.online { startOffline(desc: r.desc, project: r.project, at: Date()); return }
        let f = DateFormatter(); f.dateFormat = "HH:mm"
        let now = f.string(from: Date())
        requestDefaultScroll()
        // Resumed from a past day: the new timer runs today, so go there.
        act("/api/fill", Bodies.fill(desc: r.desc, start: now, end: "", project: r.project),
            onOk: { [weak self] _ in
                guard let self else { return }
                if self.dayOffset != 0 { self.dayOffset = 0; return }
                self.showStarted(desc: r.desc, project: r.project, at: Date()) }) { _ in
            "▶ resumed" + (r.project.isEmpty ? "" : " → " + r.project) + " ✓" }
    }
    func split(_ r: TimelineRow, mode: String) {
        requestDefaultScroll()
        act("/api/split", dated(Bodies.split(id: r.id, mode: mode))) { _ in mode == "top" ? "split ▲ ✓" : "split ▼ ✓" }
    }
    /// Delete a Toggl entry (2026-10-25, left-swipe 🗑 after a confirm). The
    /// server refuses running / already-logged / cross-midnight entries, as
    /// the terminal janus's ^X does for running ones. Online only: a queued
    /// delete replayed later could hit an entry edited in the meantime.
    func delete(_ r: TimelineRow) {
        if r.isEvent {
            // Calendar event (2026-10-25): Google-hosted ones are deleted from the
            // calendar, Outlook ones hidden from janus (read-only from here).
            act("/api/delete-event", dated(Bodies.deleteEvent(r))) { d in
                (d.string("mode") == "deleted" ? "deleted from calendar: " : "hidden: ") + r.title + " ✓" }
            return
        }
        act("/api/delete", dated(["id": r.id])) { _ in "deleted " + r.desc + " ✓" }
    }

    // ── optimistic start (2026-10-25: "when new time entry created, if it is
    // current it needs to immediately propagate to the current entry box") ──
    /// A typed command that plainly starts a timer → (desc, project, start).
    /// Same shape parseStart accepts, minus what tg-fast reads differently:
    /// "stop", and any 3-4 digit run (a trailing start time; mobile.py's
    /// _is_plain_switch uses the same test). /d and batches reconcile on reload.
    static func optimisticStart(_ t: String, now: Date) -> (String, String, Date)? {
        guard let (raw, at) = parseStart(t, now: now) else { return nil }
        let (desc, project) = stripAt(raw)
        if desc.isEmpty || desc.lowercased() == "stop" || desc.hasSuffix(" /d") { return nil }
        if desc.range(of: #"\d{3,4}"#, options: .regularExpression) != nil { return nil }
        return (desc, project, at)
    }
    /// "coffee @epcn" → ("coffee", "epcn"). The server resolves the code; this
    /// only feeds the placeholder row.
    static func stripAt(_ t: String) -> (String, String) {
        var project = ""
        let words = t.split(separator: " ").filter { w in
            if w.hasPrefix("@") && w.count > 1 { project = String(w.dropFirst()); return false }
            return true
        }
        return (words.joined(separator: " "), project)
    }
    /// Put the just-started timer on screen now: the previous running row
    /// stops at `at`, a placeholder running row (id "pending-…", inert until
    /// the reload swaps in the real Toggl entry) makes `running` non-nil.
    func showStarted(desc: String, project: String, at: Date) {
        let now = Self.hhmm(at)
        var out = rows.map { x -> TimelineRow in
            guard x.running else { return x }
            var y = x; y.running = false; y.end = now; return y
        }
        var r = TimelineRow(type: "entry")
        r.id = "pending-" + UUID().uuidString; r.desc = desc; r.project = project
        r.color = rows.last { $0.isEntry && $0.project == project && !$0.color.isEmpty }?.color ?? ""
        r.start = now; r.end = "now"; r.running = true
        out.append(r)
        rows = out
    }

    func projectCodes() async -> [String] {
        if case .ok(let j) = await api.get(base + "/api/projects") { return Parsers.projectCodes(j) }
        return []
    }
}

// ── offline timers (2026-10-22 /bug: "when offline you should continue the
// time that was on, then allow me to create new timers as needed, then sync
// back up once online") ─────────────────────────────────────────────────────
// The running timer keeps ticking on screen (its row and the timer card read
// the start time, not the network). A timer started offline closes whatever
// was running at that moment: a server entry gets a queued /api/edit setting
// its end, a local one a queued closed /api/fill. The one still open when the
// network returns is sent last as an open /api/fill (a live Toggl timer from
// its real start). Everything persists, so a relaunch on the plane keeps it.
// Points: stopping offline only stops; swipe the synced entry to log 分.
extension JanusStore {
    struct LocalTimer: Codable {
        var id: String
        var desc: String
        var project: String
        var start: Date
        var end: Date? = nil
        var queued = false
    }
    private static let timersKey = "janus.localTimers"
    private static let stoppedKey = "janus.stoppedServer"

    var localTimers: [LocalTimer] {
        get { UserDefaults.standard.data(forKey: Self.timersKey).flatMap { try? JSONDecoder().decode([LocalTimer].self, from: $0) } ?? [] }
        set { UserDefaults.standard.set(try? JSONEncoder().encode(newValue), forKey: Self.timersKey) }
    }
    /// Server entries stopped offline: id → "HH:MM" end.
    var stoppedServer: [String: String] {
        get { UserDefaults.standard.dictionary(forKey: Self.stoppedKey) as? [String: String] ?? [:] }
        set { UserDefaults.standard.set(newValue, forKey: Self.stoppedKey) }
    }

    static func hhmm(_ d: Date) -> String { let f = DateFormatter(); f.dateFormat = "HH:mm"; return f.string(from: d) }
    static func todayAt(_ hhmm: String) -> Date? {
        let p = hhmm.replacingOccurrences(of: ":", with: "")
        guard p.count == 4, let h = Int(p.prefix(2)), let m = Int(p.suffix(2)), h < 24, m < 60 else { return nil }
        return Calendar.current.date(bySettingHour: h, minute: m, second: 0, of: Date())
    }

    /// "coffee @epcn" → (desc, now); "1840 coffee" → (desc, 18:40). nil for
    /// anything else (ranges, +N, /d, batches): those queue as typed.
    static func parseStart(_ t: String, now: Date) -> (String, Date)? {
        if t.hasPrefix("/") || t.hasPrefix("+") || t.contains(",") || t.contains("，") { return nil }
        if t.range(of: #"\b\d{4}-\d{4}\b"#, options: .regularExpression) != nil { return nil }
        let parts = t.split(separator: " ", maxSplits: 1).map(String.init)
        if parts.count == 2, parts[0].count == 4, let at = todayAt(parts[0]) { return (parts[1], at) }
        if let (desc, at) = trailingStart(t) { return (desc, at) }
        return (t, now)
    }

    /// "biking lesson 1626-" / "… 1626" / "… 1626- @xk87" → ("biking lesson
    /// [@xk87]", 16:26): a trailing HHMM, optionally open-ended with "-",
    /// is the start of a still-running entry (2026-10-25; tg-fast parses
    /// the same forms server-side).
    static func trailingStart(_ t: String) -> (String, Date)? {
        guard let r = t.range(of: #"\s(\d{4})\s*-?(?=(\s+@\S+)?\s*$)"#, options: .regularExpression)
        else { return nil }
        let hhmm = t[r].filter(\.isNumber)
        guard let at = todayAt(String(hhmm)) else { return nil }
        let desc = (String(t[..<r.lowerBound]) + String(t[r.upperBound...]))
            .trimmingCharacters(in: .whitespaces)
        return desc.isEmpty ? nil : (desc, at)
    }

    func startOffline(desc: String, project: String, at start: Date) {
        closeRunning(at: start)
        var ts = localTimers
        ts.append(LocalTimer(id: "local-" + UUID().uuidString, desc: desc, project: project, start: start))
        localTimers = ts
        rowsChangedOffline("▶ \(desc) · offline, syncs when back online")
    }

    func stopOffline(_ r: TimelineRow) {
        closeRunning(at: Date())
        rowsChangedOffline("■ stopped offline · swipe to log 分 once synced")
    }

    /// End whatever is running at `t`: the open local timer (queue its closed
    /// fill) or the server's running entry (queue an edit with its end).
    private func closeRunning(at t: Date) {
        var ts = localTimers
        if let i = ts.lastIndex(where: { $0.end == nil }) {
            ts[i].end = t
            let lt = ts[i]
            if t.timeIntervalSince(lt.start) >= 60 {
                enqueueOffline("/api/fill", Bodies.fill(desc: lt.desc, start: Self.hhmm(lt.start), end: Self.hhmm(t),
                                                        project: lt.project), label: "fill \(lt.desc)")
            }
            localTimers = ts
        } else if let r = rows.first(where: { $0.running && !$0.isLocal }) {
            enqueueOffline("/api/edit", Bodies.edit(id: r.id, desc: r.desc, start: r.start, end: Self.hhmm(t),
                                                    project: r.project), label: "stop \(r.desc)")
            var s = stoppedServer; s[r.id] = Self.hhmm(t); stoppedServer = s
        }
    }

    private func enqueueOffline(_ path: String, _ body: [String: Any], label: String) {
        outbox.enqueue(url: base + path, body: body, label: label)
        pending = outbox.count
    }

    private func rowsChangedOffline(_ msg: String) {
        rows = overlay((timeline?.rows ?? rows).filter { !$0.isLocal })
        Platform.haptic(.success)
        toast.show(msg)
    }

    /// Server rows with offline edits applied, plus the local timers.
    func overlay(_ server: [TimelineRow]) -> [TimelineRow] {
        let stopped = stoppedServer
        let locals = localTimers
        guard !stopped.isEmpty || !locals.isEmpty else { return server }
        var out = server.map { r -> TimelineRow in
            guard let end = stopped[r.id], r.running else { return r }
            var y = r; y.running = false; y.end = end; return y
        }
        for lt in locals {
            var r = TimelineRow(type: "entry")
            r.id = lt.id; r.desc = "⇡ " + lt.desc; r.project = lt.project
            r.start = Self.hhmm(lt.start)
            r.end = lt.end.map(Self.hhmm) ?? "now"
            r.running = lt.end == nil
            r.minutes = Int(((lt.end ?? Date()).timeIntervalSince(lt.start)) / 60)
            out.append(r)
        }
        return out
    }
}

/// A pending points question for a swipe the server couldn't price.
struct PointsAsk {
    let path: String
    let body: [String: Any]
    let desc: String
    let minutes: Int
    let current: Int?          // set when correcting an already-logged row
    let toastText: (JSON) -> String
}
