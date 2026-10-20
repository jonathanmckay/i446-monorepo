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

    private let api = Api()
    private var loadedAt: Date? = nil
    private var cachedAt: Date? = nil
    private var base: String { InkwellConfig.janusBase }
    private let toast = Toaster.shared

    init() {
        if let (j, at) = PayloadCache.load("janus") {
            let t = Parsers.timeline(j); timeline = t; rows = t.rows; cachedAt = at
        }
    }

    /// The live Toggl entry, if any (the top card + what the command bar acts on).
    var running: TimelineRow? { rows.first { $0.running } }

    var title: String {
        let pts = timeline?.points.map { "\($0)分" } ?? "–分"
        let tracked = timeline.map { String(format: "%d:%02d", $0.trackedMin / 60, $0.trackedMin % 60) } ?? "0:00"
        return "\(pts) · janus · \(tracked) tracked"
    }
    var status: String {
        if let e = lastError { return "⚠ " + e }
        if !loadedOnce, let at = cachedAt { return "cached \(PayloadCache.clock(at)) · refreshing…" }
        return base
    }

    func reload() { Task { await load() } }
    /// Same rule as DtdStore: a failed or never-done load retries at once on
    /// foreground; a good one only when stale.
    func refreshIfStale() {
        if lastError != nil || !loadedOnce { Task { await load() }; return }
        guard let at = loadedAt, Date().timeIntervalSince(at) > 120 else { return }
        Task { await load() }
    }

    func load() async {
        loading = true
        switch await api.get(base + "/api/timeline") {
        case .ok(let j):
            loadedOnce = true; lastError = nil; loadedAt = Date()
            let t = Parsers.timeline(j)
            timeline = t; rows = t.rows
            PayloadCache.save("janus", j)
        case .error(let m):
            lastError = m
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
    func split(_ r: TimelineRow, mode: String) {
        act("/api/split", Bodies.split(id: r.id, mode: mode)) { _ in mode == "top" ? "split ▲ ✓" : "split ▼ ✓" }
    }

    func projectCodes() async -> [String] {
        if case .ok(let j) = await api.get(base + "/api/projects") { return Parsers.projectCodes(j) }
        return []
    }
}
