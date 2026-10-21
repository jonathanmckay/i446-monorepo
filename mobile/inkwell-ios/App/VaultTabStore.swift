import Foundation
import Combine
import VaultCore

/// The vault tab's state: the offline SQLite copy of the subset, the capture
/// outbox, and the last sync outcome. Reads never touch the network; a sync
/// runs on foreground (manifest ETag → usually a 304) and after a capture.
@MainActor
final class VaultTabStore: ObservableObject {
    static let shared = VaultTabStore()

    let store: VaultStore
    let sync: VaultSync

    @Published var syncing = false
    @Published var progress: (Int, Int)? = nil
    @Published var lastReport: SyncReport? = nil
    @Published var fileCount = 0
    @Published var pendingCount = 0
    @Published var lastSyncAt: String? = nil
    /// Bumped when the landing note is (re)fetched so its view re-reads.
    @Published var landingVersion = 0
    private let api = VaultApi()
    private var landingFetchedAt: TimeInterval = 0

    private var lastSyncMonotonic: TimeInterval = 0

    init() {
        // A broken database must not take the whole app down: fall back to a
        // throwaway file and say so in the header.
        let path = VaultStore.defaultPath()
        let s = (try? VaultStore(path: path)) ?? (try! VaultStore(path: NSTemporaryDirectory() + "vault-fallback.sqlite"))
        store = s
        sync = VaultSync(store: s, api: VaultApi())
        refreshCounts()
        lastSyncAt = s.meta(VaultSync.kLastSync)
    }

    var title: String {
        "vault · \(fileCount) notes" + (pendingCount > 0 ? " · \(pendingCount) pending" : "")
    }

    var status: String {
        if !VaultConfig.hasToken { return "⚠ no token (long-press)" }
        // Quiet: only a real bulk fetch (first sync / subset change) gets a
        // progress line; routine passes stay invisible.
        if syncing, let p = progress, p.1 > 20 { return "syncing \(p.0)/\(p.1)" }
        if let r = lastReport, let e = r.error { return "⚠ \(e)" }
        var bits: [String] = []
        if let g = lastReport?.generatedAt ?? store.meta(VaultSync.kGenerated) { bits.append("ix " + VaultTabStore.clock(g)) }
        if let r = lastReport { bits.append(r.summary) }
        else if let l = lastSyncAt { bits.append("synced " + VaultTabStore.clock(l)) }
        return bits.isEmpty ? VaultConfig.base : bits.joined(separator: " · ")
    }

    func refreshCounts() {
        fileCount = store.fileCount
        pendingCount = store.pendingCount
    }

    /// Open-in-1s (2026-10-20): fetch the ONE note the root screen shows
    /// straight from /file, before and independent of the full sync, so a
    /// fresh install paints new-notes in one round trip instead of after
    /// hundreds. Stored through the same upsert the sync uses; the next
    /// manifest pass reconciles mtime/sha. Re-fetched at most once a minute.
    func fetchLanding(_ path: String, force: Bool = false) {
        guard VaultConfig.hasToken, !store.pendingTargets().contains(path) else { return }
        let now = ProcessInfo.processInfo.systemUptime
        guard force || now - landingFetchedAt > 60 else { return }
        landingFetchedAt = now
        Task {
            if case .ok(let f) = await api.file(path) {
                let e = ManifestEntry(p: path, m: Int(Date().timeIntervalSince1970), s: f.body.utf8.count,
                                      h: f.manifestSha.isEmpty ? f.sha : f.manifestSha)
                try? store.upsert(e, body: f.body, sha: f.sha)
                refreshCounts()
                landingVersion += 1
            }
        }
    }

    /// Manual refresh of the one note the inbox shows. Unsent local edits take
    /// the full sync instead, since the outbox has to drain before the server
    /// copy can be trusted.
    func refreshLanding(_ path: String) {
        if store.pendingTargets().contains(path) { runSync(force: true); return }
        fetchLanding(path, force: true)
    }

    /// Foreground hook: cheap when nothing changed (one 304).
    /// Persisted staleness (2026-10-20: "indexing on every startup"): the
    /// last sync time lives in the db, so a relaunch inside the window does
    /// no network at all.
    func syncIfStale(maxAge: TimeInterval = 900) {
        if let l = store.meta(VaultSync.kLastSync), let d = Capture.stamp.date(from: l),
           Date().timeIntervalSince(d) < maxAge { return }
        guard ProcessInfo.processInfo.systemUptime - lastSyncMonotonic > maxAge else { return }
        runSync(force: false)
    }

    /// Full-text edit of an allow-listed note (new-notes): local copy first
    /// (instant, offline), one coalesced put in the outbox, sync shortly after.
    func saveNote(_ path: String, text: String) {
        let base = store.sha(path)
        let e = ManifestEntry(p: path, m: Int(Date().timeIntervalSince1970), s: text.utf8.count,
                              h: base ?? "")   // keep the base sha until the server confirms
        try? store.upsert(e, body: text, sha: base ?? "")
        try? store.enqueue(Capture(title: "put", body: text, target: path, mode: "put", baseSha: base))
        refreshCounts()
        saveDebounce?.cancel()
        saveDebounce = Task { [weak self] in
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            self?.runSync(force: false)
        }
    }
    private var saveDebounce: Task<Void, Never>? = nil

    func runSync(force: Bool) {
        guard !syncing, VaultConfig.hasToken else { return }
        syncing = true
        progress = nil
        sync.progress = { [weak self] d, t in Task { @MainActor in self?.progress = (d, t) } }
        // Background priority (2026-10-20: "indexing in the background"): the
        // manifest is one 304 when nothing changed, only changed files are
        // fetched (sha diff in VaultStore.plan), FTS is per-file on upsert.
        Task(priority: .background) {
            let r = await sync.run(force: force)
            lastReport = r
            lastSyncMonotonic = ProcessInfo.processInfo.systemUptime
            lastSyncAt = store.meta(VaultSync.kLastSync)
            syncing = false
            progress = nil
            refreshCounts()
        }
    }

    /// Capture: lands in the local outbox first (instant, offline), then a
    /// sync tries to push it. Nothing here can lose the text.
    @discardableResult
    func capture(_ text: String, title: String = "", target: String? = nil) -> Bool {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return false }
        do {
            try store.enqueue(Capture(title: title, body: body, target: target))
        } catch {
            return false
        }
        if let t = target {
            // Optimistic local append so the note shows the line at once; the
            // server's copy replaces it on the next fetch (same date-line rule).
            let cur = store.read(t) ?? ""
            let day = VaultTabStore.dayStamp()
            var block = cur.isEmpty || cur.hasSuffix("\n\n") ? "" : (cur.hasSuffix("\n") ? "\n" : "\n\n")
            if !cur.split(separator: "\n").contains(where: { $0.trimmingCharacters(in: .whitespaces) == day }) { block += day + "\n" }
            block += body + "\n"
            let e = ManifestEntry(p: t, m: Int(Date().timeIntervalSince1970), s: (cur + block).utf8.count, h: "local-" + UUID().uuidString)
            try? store.upsert(e, body: cur + block, sha: e.h)
            landingVersion += 1
        }
        refreshCounts()
        runSync(force: false)
        return true
    }

    static func dayStamp(_ d: Date = Date()) -> String {
        let f = DateFormatter(); f.dateFormat = "yyyy.MM.dd"; return f.string(from: d)
    }

    // Reads (synchronous, local).
    func list(_ dir: String) -> [DirEntry] { store.list(dir) }
    func recent() -> [DirEntry] { store.recent(limit: 60) }
    func search(_ q: String) -> [SearchHit] { store.search(q) }
    func read(_ path: String) -> String? { store.read(path) }
    func resolve(_ target: String) -> String? { store.resolve(wikilink: target) }
    func captures() -> [Capture] { store.captures(limit: 50) }

    static func clock(_ iso: String) -> String {
        // "2026-10-19T14:05:34Z" or local "2026-10-19T07:05:34" → "07:05"
        var s = iso
        var utc = false
        if s.hasSuffix("Z") { s.removeLast(); utc = true }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        f.timeZone = utc ? TimeZone(identifier: "UTC") : .current
        guard let d = f.date(from: s) else { return iso }
        let out = DateFormatter()
        out.dateFormat = "HH:mm"
        return out.string(from: d)
    }
}
