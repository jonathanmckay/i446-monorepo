import Foundation
import InkwellCore

/// dtd: today's task list. Right-swipe (leading edge) = complete via the real
/// /did on Ix (closes Todoist + writes Neon). Trailing edge = start / +1d /
/// ⏰ delay. Same contract as tools/dtd/dtd.py's web page and the Android client.
@MainActor
final class DtdStore: ObservableObject {
    @Published private(set) var tasks: [DtdTask] = []
    @Published private(set) var summary = DtdSummary(points: 0, done: 0)
    @Published private(set) var lastError: String? = nil
    @Published private(set) var loadedOnce = false
    @Published private(set) var loading = false
    @Published private(set) var inFlight: Set<String> = []   // ids with a POST outstanding — no double swipes
    @Published private(set) var scrollTick = 0               // bumps whenever the list should jump to the top

    private let api = Api()
    private var activeId: String? = nil                      // id of the current top/active row — detects a new claim
    private var loadedAt: Date? = nil
    private var cachedAt: Date? = nil
    private var base: String { InkwellConfig.dtdBase }
    private let toast = Toaster.shared

    init() {
        // Paint the last known list immediately; the fetch in .task refreshes it.
        if let (j, at) = PayloadCache.load("dtd"), PayloadCache.isToday(at) {
            tasks = Parsers.tasks(j); summary = Parsers.summary(j); cachedAt = at
        }
    }

    var title: String { "\(summary.points)分 · dtd · \(summary.done) done · \(tasks.count) left" }
    var agentCount: Int { tasks.filter { $0.agent }.count }
    var status: String {
        if let e = lastError { return "⚠ " + e }
        if !loadedOnce, let at = cachedAt { return "cached \(PayloadCache.clock(at)) · refreshing…" }
        return base
    }

    func reload() { Task { await load(refresh: true) } }

    /// Foreground refresh: retry at once if the last load failed or never
    /// happened (app opened on a plane, tailnet came up later — 2026-10-19),
    /// otherwise only if the list is older than the server's own cache TTL.
    func refreshIfStale() {
        if lastError != nil || !loadedOnce { Task { await load(refresh: false) }; return }
        guard let at = loadedAt, Date().timeIntervalSince(at) > 180 else { return }
        Task { await load(refresh: false) }
    }

    func load(refresh: Bool) async {
        loading = true
        let url = base + "/api/tasks" + (refresh ? "?refresh=1" : "")
        switch await api.get(url) {
        case .ok(let j):
            loadedOnce = true
            lastError = nil
            loadedAt = Date()
            tasks = activeFirst(Parsers.tasks(j))
            summary = Parsers.summary(j)
            PayloadCache.save("dtd", j)
            // A task that went active since the last load (e.g. claimed from
            // another client) bubbles up here — scroll so it's visible.
            noteActive(scrollIfChanged: true)
        case .error(let m):
            lastError = m
        }
        loading = false
    }

    // ── complete ─────────────────────────────────────────────────────────
    /// Optimistic: the row leaves immediately (like the web's fly()), but a
    /// failure puts it BACK with a persistent retry.
    func complete(_ t: DtdTask, value: String?) {
        guard let pos = index(of: t), !inFlight.contains(t.id) else { return }
        inFlight.insert(t.id)
        removeRow(at: pos)
        summary.points += t.points ?? 0
        summary.done += 1
        Platform.haptic(.commit)
        Task {
            let r = await Platform.keepAlive { await api.post(base + "/api/done", Bodies.done(t, value: value)) }
            inFlight.remove(t.id)
            switch r {
            case .ok(let j):
                let pts = t.points ?? 0
                let msg = j.bool("already") ? "already done today"
                    : j.bool("closed") ? "+\(pts) 分 ✓"
                    : j.bool("future_skipped") ? "+\(pts) 分 · recurring"
                    : "+\(pts) 分"
                toast.show(msg)
            case .error(let m):
                summary.points -= t.points ?? 0
                summary.done -= 1
                restoreRow(t, at: pos)
                Platform.haptic(.failure)
                toast.show("not completed: \(m)", retry: { [weak self] in self?.complete(t, value: value) })
            }
        }
    }

    private func index(of t: DtdTask) -> Int? { tasks.firstIndex { $0.id == t.id } }
    private func removeRow(at i: Int) { tasks.remove(at: i) }
    private func restoreRow(_ t: DtdTask, at i: Int) {
        if index(of: t) != nil { return }
        tasks.insert(t, at: min(max(0, i), tasks.count))
    }

    // ── trailing actions: start / +1d / ⏰ ───────────────────────────────
    /// Starting a task makes its Toggl timer the live one. Optimistically mark
    /// it ▶, float it to the top (only one timer runs at a time, so any other
    /// ▶ is cleared), and scroll the list up so the now-active row is visible.
    /// A failure reloads to let the server's truth reassert the order.
    func start(_ t: DtdTask) {
        Platform.haptic(.commit)   // medium impact the instant the timer starts
        promoteToTop(t, running: true)
        Task {
            switch await api.post(base + "/api/start", Bodies.start(t)) {
            case .ok(let j):
                let clean = j.string("clean", t.title)
                let proj = j.string("project")
                toast.show("▶ " + clean + (proj.isEmpty ? "" : " → \(proj)"))
            case .error(let m):
                toast.show("not started: \(m)")
                await load(refresh: false)
            }
        }
    }

    /// Partition active rows (▶ live timer / 😈 claimed) to the front, keeping
    /// each group's existing relative order. Mirrors the web page's row order.
    private func activeFirst(_ list: [DtdTask]) -> [DtdTask] {
        list.filter { $0.isActive } + list.filter { !$0.isActive }
    }

    /// Record which row is active now; if it changed to a new task, ask the
    /// list to scroll to the top.
    private func noteActive(scrollIfChanged: Bool) {
        let top = tasks.first { $0.isActive }?.id
        if scrollIfChanged, let top, top != activeId { scrollTick &+= 1 }
        activeId = top
    }

    /// Optimistically move a task to index 0 and flag it running (clearing any
    /// other ▶), then trigger a scroll to the top.
    private func promoteToTop(_ t: DtdTask, running: Bool) {
        guard let i = index(of: t) else { return }
        tasks.remove(at: i)
        if running { tasks = tasks.map { $0.running ? $0.withRunning(false) : $0 } }
        tasks.insert(t.withRunning(running), at: 0)
        activeId = t.id
        scrollTick &+= 1
    }

    func delayDay(_ t: DtdTask) {
        delay(t, path: "/api/delay-day", body: Bodies.byId(t)) { "🗓 → " + $0.string("target_date", "tomorrow") }
    }
    /// The CLI schedule screen's day rows: 0 = next occurrence, else N days out.
    func delayDays(_ t: DtdTask, days n: Int) {
        delay(t, path: "/api/delay-day", body: Bodies.delayDays(t, days: n)) { "🗓 → " + $0.string("target_date", "tomorrow") }
    }
    func delayHour(_ t: DtdTask, hour: Int) {
        delay(t, path: "/api/delay-hour", body: Bodies.delayHour(t, hour: hour)) { _ in
            "⏰ → " + String(format: "%02d:00", hour) }
    }
    func delayMinutes(_ t: DtdTask, minutes m: Int) {
        let label = m % 60 == 0 ? "+\(m / 60)h" : "+\(m)m"
        delay(t, path: "/api/delay-minutes", body: Bodies.delayMinutes(t, minutes: m)) { _ in "⏰ → \(label)" }
    }

    /// Delays hide the task until its delayed time, so the row leaves like a
    /// completion does (web: "delaying a day/block hides the task").
    private func delay(_ t: DtdTask, path: String, body: [String: Any], toastText: @escaping (JSON) -> String) {
        guard let pos = index(of: t), !inFlight.contains(t.id) else { return }
        inFlight.insert(t.id)
        removeRow(at: pos)
        Task {
            let r = await api.post(base + path, body)
            inFlight.remove(t.id)
            switch r {
            case .ok(let j): toast.show(toastText(j))
            case .error(let m):
                restoreRow(t, at: pos)
                toast.show("not delayed: \(m)", retry: { [weak self] in self?.delay(t, path: path, body: body, toastText: toastText) })
            }
        }
    }

    /// Fetched fresh each time the sheet opens so the block list is never stale.
    func delayOptions() async -> DelayOptions? {
        if case .ok(let j) = await api.get(base + "/api/delay-options") { return Parsers.delayOptions(j) }
        return nil
    }

    // ── add ──────────────────────────────────────────────────────────────
    func add(_ content: String) {
        let c = content.trimmingCharacters(in: .whitespaces)
        guard !c.isEmpty else { return }
        Task {
            switch await api.post(base + "/api/add", Bodies.add(c)) {
            case .ok: toast.show("+ added"); await load(refresh: true)
            case .error(let m): toast.show("not added: \(m)")
            }
        }
    }
}
