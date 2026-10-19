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
        if syncing, let p = progress, p.1 > 0 { return "syncing \(p.0)/\(p.1)" }
        if syncing { return "syncing…" }
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

    /// Foreground hook: cheap when nothing changed (one 304).
    func syncIfStale(maxAge: TimeInterval = 60) {
        guard ProcessInfo.processInfo.systemUptime - lastSyncMonotonic > maxAge else { return }
        runSync(force: false)
    }

    func runSync(force: Bool) {
        guard !syncing, VaultConfig.hasToken else { return }
        syncing = true
        progress = nil
        sync.progress = { [weak self] d, t in Task { @MainActor in self?.progress = (d, t) } }
        Task {
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
    func capture(_ text: String, title: String = "") -> Bool {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return false }
        do {
            try store.enqueue(Capture(title: title, body: body))
        } catch {
            return false
        }
        refreshCounts()
        runSync(force: false)
        return true
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
