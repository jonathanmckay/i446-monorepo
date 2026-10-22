import Foundation
import InkwellCore

/// janus: today's timeline (Toggl entries, gaps, calendar events, 地支
/// dividers). Leading swipe: entry → log 分, running → stop+log, event →
/// convert to a Toggl entry, gap → fill dialog. Trailing swipe on an entry →
/// edit/split. Every action reloads the whole timeline (no optimism — the
/// server owns the ledger that says what is already logged).
@MainActor
final class JanusStore: ObservableObject {
    @Published private(set) var rows: [TimelineRow] = []
    @Published private(set) var timeline: Timeline? = nil
    @Published private(set) var lastError: String? = nil
    @Published private(set) var loadedOnce = false
    @Published private(set) var loading = false
    @Published private(set) var busy = false   // one action at a time; did-fast writes aren't idempotent
    /// 0 = today, -1 = yesterday, … (≤0). The back/forward day view (the CLI's
    /// ctrl+/ / ctrl+=). A past day is read-only: the write endpoints all
    /// target today on the server, so the view hides the swipes and bottom bar.
    @Published private(set) var dayOffset = 0

    private let api = Api()
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
            if t.date == PayloadCache.todayISO() { timeline = t; rows = t.rows; cachedAt = at }
        }
    }

    /// The live Toggl entry, if any (the top card + what the command bar acts on).
    var running: TimelineRow? { rows.first { $0.running } }

    /// A past day is read-only. Keyed off dayOffset (authoritative on the
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

    func prevDay() { dayOffset -= 1; Task { await load() } }
    func nextDay() { guard dayOffset < 0 else { return }; dayOffset += 1; Task { await load() } }
    func goToday() { guard dayOffset != 0 else { return }; dayOffset = 0; Task { await load() } }

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
        return "\(pts) · janus · \(tracked) tracked"
    }
    var status: String {
        if let e = lastError { return "⚠ " + e }
        if !loadedOnce, let at = cachedAt { return "cached \(PayloadCache.clock(at)) · refreshing…" }
        return ""
    }

    func reload() { Task { await load() } }
    /// Same rule as DtdStore: a failed or never-done load retries at once on
    /// foreground; a good one only when stale.
    func refreshIfStale() {
        if lastError != nil || !loadedOnce { Task { await load() }; return }
        guard let at = loadedAt, Date().timeIntervalSince(at) > Self.cacheTTL else { return }
        Task { await load() }
    }

    func load() async {
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
                    let t = Parsers.timeline(j); timeline = t; rows = t.rows
                }
            }
        }
        let fullURL = base + "/api/timeline" + (offsetQ.isEmpty ? "" : "?" + offsetQ)
        switch await api.get(fullURL) {
        case .ok(let j):
            guard seq == loadSeq else { loading = false; return }
            loadedOnce = true; lastError = nil; loadedAt = Date()
            let t = Parsers.timeline(j)
            timeline = t; rows = t.rows
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
        if r.isEvent {
            act("/api/convert-event", Bodies.convertEvent(r)) { d in
                d.string("mode") == "logged" ? "+\(r.minutes)m → \(d.string("step", "neon")) ✓" : "tracking started ✓" }
        } else if r.running {
            act("/api/done-current", Bodies.doneCurrent(r)) { d in "stopped + logged → \(d.string("step", "neon")) ✓" }
        } else if r.logged {
            toast.show("already logged")
        } else {
            act("/api/log", Bodies.log(r)) { d in
                if d.bool("already") { return "already logged" }
                let extra = d.strings("tag_steps").joined(separator: ", ")
                return "+\(r.minutes)m → \(d.string("step", "neon"))" + (extra.isEmpty ? "" : " + \(extra)") + " ✓"
            }
        }
    }

    /// One POST, then a full reload. `needs_agent` is the server saying it
    /// has no route for this description — the web sends you to desktop /did.
    func act(_ path: String, _ body: [String: Any], toastText: @escaping (JSON) -> String) {
        guard !busy else { return }
        busy = true
        Platform.haptic(.commit)
        Task {
            let r = await Platform.keepAlive { await api.post(base + path, body) }
            busy = false
            switch r {
            case .ok(let j): toast.show(toastText(j)); await load()
            case .error(let m):
                Platform.haptic(.failure)
                toast.show(m.contains("needs_agent") ? "no route — use /did on desktop" : m)
            }
        }
    }

    /// The CLI's typed-command path (web command bar): "coffee @epcn",
    /// "1400 coffee", "0000-0550 睡觉", "+5", "/d <task>". Server routes each
    /// comma-separated part to tg-fast or did-fast.
    func run(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        act("/api/run", ["text": t]) { d in d.string("msg", "ok ✓") }
    }

    func fill(desc: String, start: String, end: String, project: String = "") {
        act("/api/fill", Bodies.fill(desc: desc, start: start, end: end, project: project)) { d in
            d.bool("running") ? "▶ started" + (d.string("project").isEmpty ? "" : " → " + d.string("project")) + " ✓" : "saved ✓" }
    }
    func edit(_ r: TimelineRow, desc: String, start: String, end: String, project: String) {
        act("/api/edit", Bodies.edit(id: r.id, desc: desc, start: start, end: end, project: project)) { _ in "edited ✓" }
    }
    /// Resume an entry (janus full left-swipe): start a fresh ongoing Toggl
    /// timer carrying the same description + project. There is no dedicated
    /// server route for resuming an *entry* — only calendar events resume via
    /// /api/convert-event — so this reuses /api/fill with a blank end, which
    /// is exactly what "start now" does and which the server returns as a
    /// running entry. Tags aren't carried (fill has no tags field), matching
    /// the existing fill / start-now behaviour.
    func resume(_ r: TimelineRow) {
        let f = DateFormatter(); f.dateFormat = "HH:mm"
        let now = f.string(from: Date())
        act("/api/fill", Bodies.fill(desc: r.desc, start: now, end: "", project: r.project)) { _ in
            "▶ resumed" + (r.project.isEmpty ? "" : " → " + r.project) + " ✓" }
    }
    func split(_ r: TimelineRow, mode: String) {
        act("/api/split", Bodies.split(id: r.id, mode: mode)) { _ in mode == "top" ? "split ▲ ✓" : "split ▼ ✓" }
    }

    func projectCodes() async -> [String] {
        if case .ok(let j) = await api.get(base + "/api/projects") { return Parsers.projectCodes(j) }
        return []
    }
}
