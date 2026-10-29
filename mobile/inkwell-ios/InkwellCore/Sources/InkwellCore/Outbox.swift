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
        /// Caller's handle on the write (e.g. a local task id), returned by
        /// flush once the item lands so the UI can retire its local copy.
        public var ref: String? = nil
    }

    private let key: String
    private var flushing = false
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

    public func enqueue(url: String, body: [String: Any], label: String, ref: String? = nil) {
        guard let data = try? JSONSerialization.data(withJSONObject: body) else { return }
        save(items + [Item(id: UUID(), url: url, body: data, label: label, at: Date(), ref: ref)])
    }

    /// Drop queued items carrying `ref` (a local-only thing the user undid).
    public func remove(ref: String) { save(items.filter { $0.ref != ref }) }
    public func has(ref: String) -> Bool { items.contains { $0.ref == ref } }

    public func clear() { UserDefaults.standard.removeObject(forKey: key) }

    /// True for transport errors that mean "never reached the server".
    public static func isOffline(_ message: String) -> Bool {
        message == "offline" || message.hasPrefix("unreachable")
    }

    /// Replay in order. Stops at the first still-offline item (keeps it and
    /// everything after); a server-side rejection drops that item and reports
    /// it, since retrying a refused write forever would wedge the queue.
    /// Returns (sent, rejected "label: error" lines, refs of items that landed).
    @discardableResult
    public func flush(_ api: Api) async -> (sent: Int, rejected: [String], refs: [String]) {
        // One flush at a time per queue: reconnect + foreground + load can all
        // fire together, and two concurrent replays would double-send.
        guard !flushing else { return (0, [], []) }
        flushing = true
        defer { flushing = false }
        var queue = items
        var sent = 0
        var rejected: [String] = []
        var refs: [String] = []
        var ids: [String: String] = [:]          // ref → server id an earlier item returned
        while let it = queue.first {
            var body = (try? JSONSerialization.jsonObject(with: it.body)) as? [String: Any] ?? [:]
            // A value "$ref:<ref>" means "the id the item with that ref got
            // back" (a done on a task added offline). No id came back → drop
            // the key and let the server match by content.
            for (k, v) in body {
                guard let s = v as? String, s.hasPrefix("$ref:") else { continue }
                if let id = ids[String(s.dropFirst(5))], !id.isEmpty { body[k] = id } else { body.removeValue(forKey: k) }
            }
            switch await api.post(it.url, body) {
            case .ok(let j) where j.bool("needs_points"):
                // A queued /api/log or /api/done-current for an entry with no
                // task [N] and no time-based habit (lib/janus_done.py "ask"):
                // the server credited nothing and is waiting for an answer the
                // replay can't give. Counting it as sent showed ✓ and dropped
                // the 分 silently (v172 review); report it instead.
                rejected.append("\(it.label): needs points — swipe it again")
            case .ok(let j):
                sent += 1
                if let r = it.ref { ids[r] = j.string("id") }
            case .error(let m):
                if Outbox.isOffline(m) || m == "timed out" { save(queue); return (sent, rejected, refs) }
                rejected.append("\(it.label): \(m)")
            }
            if let r = it.ref { refs.append(r) }
            queue.removeFirst()
            save(queue)
        }
        return (sent, rejected, refs)
    }
}
