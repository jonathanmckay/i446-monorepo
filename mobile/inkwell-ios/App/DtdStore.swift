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
    @Published var filter = ""                               // fuzzy filter box text (wave 2 #1)

    private let api = Api()
    private let outbox = Outbox("dtd")
    /// Writes queued while offline (⇡N in the title), replayed on reconnect.
    @Published private(set) var pending = 0
    private var activeId: String? = nil                      // id of the current top/active row — detects a new claim
    private var loadedAt: Date? = nil
    private var cachedAt: Date? = nil
    private var base: String { InkwellConfig.dtdBase }
    private let toast = Toaster.shared

    init() {
        // Paint the last known list immediately; the fetch in .task refreshes it.
        if let (j, at) = PayloadCache.load("dtd"), PayloadCache.isToday(at) {
            tasks = DtdTask.visible(Parsers.tasks(j)); summary = Parsers.summary(j); cachedAt = at
        }
        pending = outbox.count
        tasks = overlay(tasks)
    }

    // ── offline overlay (2026-10-22 /bug: "created new tasks while offline,
    // and they didn't stick") ──────────────────────────────────────────────
    // What the phone did offline must show on screen until it syncs, across
    // reloads and relaunches: tasks added offline are drawn from a local list,
    // and tasks completed/delayed offline stay hidden while their write sits
    // in the outbox. Outbox refs: "add:<local id>" for an add, "hide:<id>"
    // for a done/delay.
    private static let localKey = "dtd.localTasks"
    private struct LocalTask: Codable { let id: String; let raw: String }
    private var localTasks: [LocalTask] {
        get { (UserDefaults.standard.data(forKey: Self.localKey)).flatMap { try? JSONDecoder().decode([LocalTask].self, from: $0) } ?? [] }
        set { UserDefaults.standard.set(try? JSONEncoder().encode(newValue), forKey: Self.localKey) }
    }

    private func overlay(_ list: [DtdTask]) -> [DtdTask] {
        // Retire local tasks whose add has landed (the server list now has them).
        localTasks = localTasks.filter { outbox.has(ref: "add:" + $0.id) }
        let shown = list.filter { !outbox.has(ref: "hide:" + $0.id) }
        let locals = localTasks.filter { !outbox.has(ref: "hide:" + $0.id) }.map(Self.localRow)
        return shown + locals
    }

    /// A task typed offline, drawn like any other row ("⇡" marks it unsynced).
    private static func localRow(_ l: LocalTask) -> DtdTask {
        let code = l.raw.range(of: #"@(\w+)"#, options: .regularExpression).map { String(l.raw[$0].dropFirst()) } ?? ""
        let pts = l.raw.range(of: #"\[(\d+)\]"#, options: .regularExpression).flatMap { Int(l.raw[$0].dropFirst().dropLast()) }
        var title = l.raw
        for pat in [#"\s*\(\d+\)"#, #"\s*\[\d+\]"#, #"\s*\{\d+\}"#, #"\s*@\w+"#] {
            title = title.replacingOccurrences(of: pat, with: "", options: .regularExpression)
        }
        return DtdTask(id: l.id, raw: l.raw, title: "⇡ " + title.trimmingCharacters(in: .whitespaces),
                       est: DtdTask.resolveEst(serverEst: nil, raw: l.raw, domain: code), points: pts,
                       color: "#e6e6e0", domain: code, recurring: false, variablePrompt: false)
    }

    /// POST unless the phone knows it's offline, in which case answer
    /// "offline" at once so the caller queues instead of waiting on a timeout.
    private func send(_ path: String, _ body: [String: Any]) async -> ApiResult {
        guard NetWatch.shared.online else { return .error("offline") }
        return await Platform.keepAlive { await api.post(base + path, body) }
    }

    var title: String {
        "\(summary.points)分 · dtd · \(summary.done) done · \(tasks.count) left" + (pending > 0 ? " · ⇡\(pending)" : "")
    }

    /// The list the view renders: `tasks` narrowed by the fuzzy filter box
    /// (wave 2 #1, 2026-10-22), the native mirror of the dtd web filter. Each
    /// whitespace word must match the row's title, raw content or domain as an
    /// in-order subsequence, so a bare domain ("xk87") scopes the list and a
    /// dropped letter ("biks") still hits. Empty filter → everything.
    var visibleTasks: [DtdTask] {
        let words = filter.lowercased().split(whereSeparator: { $0.isWhitespace }).map(String.init)
        guard !words.isEmpty else { return tasks }
        return tasks.filter { t in
            let hay = (t.title + " " + t.raw + " " + t.domain).lowercased()
            return words.allSatisfy { DtdStore.fuzzyWord($0, hay) }
        }
    }

    /// True if every character of `w` appears in `hay` in order (a contiguous
    /// substring short-circuits). Same semantics as the web page's fuzzyWord.
    static func fuzzyWord(_ w: String, _ hay: String) -> Bool {
        if hay.contains(w) { return true }
        var it = hay.makeIterator()
        for ch in w {
            var hit = false
            while let h = it.next() { if h == ch { hit = true; break } }
            if !hit { return false }
        }
        return true
    }
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

    /// Replay offline writes (completions, delays, adds) in order. Called on
    /// foreground, on network return and before every load, so the list that
    /// load() fetches already reflects them.
    func flushOutbox() async {
        guard outbox.count > 0 else { return }
        let r = await outbox.flush(api)
        pending = outbox.count
        if !r.refs.isEmpty { tasks = overlay(tasks) }
        if !r.rejected.isEmpty { toast.show("sync refused: " + r.rejected.joined(separator: "; ")) }
        else if r.sent > 0 { toast.show("synced \(r.sent) offline change\(r.sent == 1 ? "" : "s") ✓") }
    }

    /// Offline: keep the write for replay and leave the optimistic UI as is.
    /// Returns false (caller rolls back) for any other failure.
    private func queueIfOffline(_ m: String, path: String, body: [String: Any], label: String,
                                ref: String? = nil) -> Bool {
        guard Outbox.isOffline(m) else { return false }
        outbox.enqueue(url: base + path, body: body, label: label, ref: ref)
        pending = outbox.count
        toast.show("offline · queued ⇡\(pending), syncs when back online")
        return true
    }

    func load(refresh: Bool) async {
        await flushOutbox()
        loading = true
        let url = base + "/api/tasks" + (refresh ? "?refresh=1" : "")
        switch await api.get(url) {
        case .ok(let j):
            loadedOnce = true
            lastError = nil
            loadedAt = Date()
            // Hide tasks deferred to later today (scheduledTime still in the
            // future, same day) before ordering — they reappear once their time
            // passes on the next refresh. Bug 2026-10-22: these leaked through.
            tasks = overlay(activeFirst(DtdTask.visible(Parsers.tasks(j))))
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
        // A task added offline has no Todoist id yet: its done carries a
        // "$ref:" the outbox swaps for the id its queued add gets back.
        var body = Bodies.done(t, value: value)
        if t.isLocal { body["id"] = "$ref:add:" + t.id; body["content"] = t.raw }
        Task {
            // A local task's done always rides the queue (behind its add).
            let r = t.isLocal ? ApiResult.error("offline") : await send("/api/done", body)
            inFlight.remove(t.id)
            switch r {
            case .ok(let j):
                let pts = t.points ?? 0
                let msg = j.bool("already") ? "already done today"
                    : j.bool("closed") ? "+\(pts) 分 ✓"
                    : j.bool("future_skipped") ? "+\(pts) 分 · recurring"
                    : "+\(pts) 分"
                toast.show(msg)
            case .error(let m) where queueIfOffline(m, path: "/api/done", body: body, label: "done \(t.title)",
                                                     ref: "hide:" + t.id):
                if NetWatch.shared.online { await flushOutbox() }
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
        guard !t.isLocal else { toast.show("added offline: start it once it syncs"); return }
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
        guard !t.isLocal else { toast.show("added offline: delay it once it syncs"); return }
        guard let pos = index(of: t), !inFlight.contains(t.id) else { return }
        inFlight.insert(t.id)
        removeRow(at: pos)
        Task {
            let r = await send(path, body)
            inFlight.remove(t.id)
            switch r {
            case .ok(let j): toast.show(toastText(j))
            case .error(let m) where queueIfOffline(m, path: path, body: body, label: "delay \(t.title)",
                                                     ref: "hide:" + t.id):
                break
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
            switch await send("/api/add", Bodies.add(c)) {
            case .ok: toast.show("+ added"); await load(refresh: true)
            case .error(let m) where Outbox.isOffline(m):
                // Draw it now, sync it later: the row is usable at once
                // (complete works; start/delay wait for the real id).
                let l = LocalTask(id: "local-" + UUID().uuidString, raw: c)
                localTasks.append(l)
                _ = queueIfOffline(m, path: "/api/add", body: Bodies.add(c), label: "add \(c)", ref: "add:" + l.id)
                tasks.append(Self.localRow(l))
            case .error(let m): toast.show("not added: \(m)")
            }
        }
    }
}

// ── edit / split / survey forms (round 3 #5 and #1, 2026-10-22) ─────────
extension DtdStore {
    /// ctrl-g prefill: name/domain/points/notes plus the allowed domain list.
    func editFields(_ t: DtdTask) async -> DtdEditFields? {
        let q = t.id.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? t.id
        guard case .ok(let j) = await Api().get(InkwellConfig.dtdBase + "/api/edit-get?id=" + q) else { return nil }
        return DtdEditFields(name: j.string("name"), domain: j.string("domain"), points: j.string("points"),
                             desc: j.string("desc"), domains: j.strings("domains"))
    }

    /// Same contract as terminal dtd's ctrl-g: edit-fast applies only what changed.
    func applyEdit(_ t: DtdTask, _ f: DtdEditFields) {
        post("/api/edit", ["id": t.id, "name": f.name, "domain": f.domain, "points": f.points, "desc": f.desc],
             ok: { _ in "edited ✓" }, fail: "not edited")
    }

    /// ctrl-p "done so far": credit part of the task now, keep the remainder.
    func split(_ t: DtdTask, pts: Int?, minutes: Int?, name: String, keep: Bool) {
        var b: [String: Any] = ["id": t.id, "keep": keep]
        if let pts { b["pts"] = pts }
        if let minutes { b["minutes"] = minutes }
        if !name.isEmpty { b["name"] = name }
        Platform.haptic(.commit)
        post("/api/split", b, ok: { j in j.string("message", "✂ split ✓") }, fail: "not split")
    }

    enum FormLookup { case form(DtdForm), none, failed }
    /// Survey card → its form schema. `.none` = the server has no form for
    /// this card (complete it normally); `.failed` = couldn't ask (do nothing:
    /// completing a survey without its answers would lose them).
    func form(_ t: DtdTask) async -> FormLookup {
        let q = t.id.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? t.id
        switch await Api().get(InkwellConfig.dtdBase + "/api/form?id=" + q) {
        case .ok(let j): return Parsers.form(j).map { .form($0) } ?? FormLookup.none
        case .error(let m) where m.contains("not JSON") || m == "HTTP 404" || m == "HTTP 200": return FormLookup.none
        case .error(let m): Toaster.shared.show("form: \(m)"); return .failed
        }
    }

    func submitForm(_ t: DtdTask, _ f: DtdForm, values: [String: String]) {
        Platform.haptic(.commit)
        post("/api/form", ["id": t.id, "form": f.name, "values": values],
             ok: { j in j.string("message", "✓ \(f.title)") }, fail: "form not saved")
    }

    private func post(_ path: String, _ body: [String: Any], ok: @escaping (JSON) -> String, fail: String) {
        Task {
            switch await Platform.keepAlive({ await Api().post(InkwellConfig.dtdBase + path, body) }) {
            case .ok(let j): Platform.haptic(.success); Toaster.shared.show(ok(j)); await load(refresh: true)
            case .error(let m): Platform.haptic(.failure); Toaster.shared.show("\(fail): \(m)")
            }
        }
    }
}

struct DtdEditFields: Identifiable {
    let id = UUID()
    var name: String
    var domain: String
    var points: String
    var desc: String
    var domains: [String]
}
