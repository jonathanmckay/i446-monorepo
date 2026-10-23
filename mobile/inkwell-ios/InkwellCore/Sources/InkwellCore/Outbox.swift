import Foundation

/// Offline write queue (round 3 #6, 2026-10-22: "janus and dtd should both
/// work offline and sync once internet is restored"). A POST that fails
/// because the phone has no route to Ix (no network, tailnet down) is kept
/// here, persisted in UserDefaults, and replayed in order on the next
/// foreground / reconnect / successful load. The UI treats a queued write as
/// done (row leaves, ⇡N in the header) so the app is usable on a plane.
///
/// Only *no-connection* failures queue. A timeout is NOT queued: the POST may
/// have reached the server (did-fast runs 10–45 s) and a replay of a
/// non-idempotent write (/api/run, /api/fill) would double it. The routes
/// that do queue are idempotent or close to it server-side: /api/done (by
/// id), /api/log (per-day ledger), delays (set a due/snooze to a value).
public final class Outbox {
    public struct Item: Codable, Equatable {
        public let id: UUID
        public let url: String
        public let body: Data
        public let label: String
        public let at: Date
    }

    private let key: String
    public init(_ name: String) { key = "outbox." + name }

    public var items: [Item] {
        guard let d = UserDefaults.standard.data(forKey: key),
              let v = try? JSONDecoder().decode([Item].self, from: d) else { return [] }
        return v
    }
    public var count: Int { items.count }

    private func save(_ v: [Item]) {
        if let d = try? JSONEncoder().encode(v) { UserDefaults.standard.set(d, forKey: key) }
    }

    public func enqueue(url: String, body: [String: Any], label: String) {
        guard let data = try? JSONSerialization.data(withJSONObject: body) else { return }
        save(items + [Item(id: UUID(), url: url, body: data, label: label, at: Date())])
    }

    public func clear() { UserDefaults.standard.removeObject(forKey: key) }

    /// True for transport errors that mean "never reached the server".
    public static func isOffline(_ message: String) -> Bool {
        message == "offline" || message.hasPrefix("unreachable")
    }

    /// Replay in order. Stops at the first still-offline item (keeps it and
    /// everything after); a server-side rejection drops that item and reports
    /// it, since retrying a refused write forever would wedge the queue.
    /// Returns (sent, rejected "label: error" lines).
    public func flush(_ api: Api) async -> (sent: Int, rejected: [String]) {
        var queue = items
        var sent = 0
        var rejected: [String] = []
        while let it = queue.first {
            let body = (try? JSONSerialization.jsonObject(with: it.body)) as? [String: Any] ?? [:]
            switch await api.post(it.url, body) {
            case .ok: sent += 1
            case .error(let m):
                if Outbox.isOffline(m) || m == "timed out" { save(queue); return (sent, rejected) }
                rejected.append("\(it.label): \(m)")
            }
            queue.removeFirst()
            save(queue)
        }
        return (sent, rejected)
    }
}
