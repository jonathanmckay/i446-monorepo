import Foundation
import InkwellCore
#if canImport(UIKit)
import UIKit
#endif

/// The UIKit-only bits, isolated so the rest of the UI typechecks on macOS
/// (`swiftc -typecheck` from CommandLineTools, before Xcode is installed).
enum Platform {
    /// Keep a POST alive for ~30 s after the phone locks. iOS suspends a
    /// foreground app a few seconds after lock; a did-fast POST (ssh + Excel,
    /// 10–45 s) would otherwise die client-side while the server finishes and
    /// the retry would double-write. (Server side, /api/done is idempotent
    /// by id and /api/log by ledger, so even the worst case is now safe.)
    static func keepAlive<T>(_ op: () async -> T) async -> T {
        #if canImport(UIKit)
        let id = await UIApplication.shared.beginBackgroundTask(expirationHandler: nil)
        let out = await op()
        await UIApplication.shared.endBackgroundTask(id)
        return out
        #else
        return await op()
        #endif
    }

    static func haptic(_ kind: Haptic) {
        #if canImport(UIKit)
        switch kind {
        case .commit: UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        case .key: UIImpactFeedbackGenerator(style: .light).impactOccurred()
        case .success: UINotificationFeedbackGenerator().notificationOccurred(.success)
        case .failure: UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
        #endif
    }
    enum Haptic { case commit, key, success, failure }
}

/// Phone beacon (lib/where.py on Ix): this phone's zone decides "today"
/// everywhere. The web page sends it on load and on visibilitychange; the
/// Android client never did, which lets travel-timezone "today" drift.
enum WhereBeacon {
    static func send() {
        Task.detached(priority: .utility) {
            _ = await Api().post(InkwellConfig.dtdBase + "/api/where", ["tz": TimeZone.current.identifier])
        }
    }
}

/// Is this phone on the tailnet? (2026-11-19: Tailscale was off for a day and
/// dtd showed an empty list, then "timed out" 45 s later.) A Tailscale tunnel
/// is a utun interface holding a 100.64.0.0/10 address; without one, a request
/// to Ix's 100.x address or its ts.net name can only hang. utun only: carriers
/// hand out the same CGNAT range on the cellular interface. Advisory: callers
/// label the failure with it and still send the request, so a wrong "down"
/// can never lock the app out.
enum Tailnet {
    static let message = "not on Tailscale"

    /// True when `base` is only reachable over the tailnet and no tunnel is up.
    static func missing(for base: String) -> Bool { needs(base) && !isUp() }

    static func needs(_ base: String) -> Bool {
        guard let host = URL(string: base)?.host?.lowercased() else { return false }
        if host.hasSuffix(".ts.net") { return true }
        let o = host.split(separator: ".").compactMap { UInt32($0) }
        return o.count == 4 && o[0] == 100 && (64...127).contains(o[1])
    }

    static func isUp() -> Bool {
        var ifap: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifap) == 0, let first = ifap else { return true }   // can't tell: don't accuse
        defer { freeifaddrs(ifap) }
        for p in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let i = p.pointee
            guard let sa = i.ifa_addr, sa.pointee.sa_family == sa_family_t(AF_INET),
                  (Int32(i.ifa_flags) & IFF_UP) != 0,
                  String(cString: i.ifa_name).hasPrefix("utun") else { continue }
            let a = sa.withMemoryRebound(to: sockaddr_in.self, capacity: 1) {
                UInt32(bigEndian: $0.pointee.sin_addr.s_addr)
            }
            if a & 0xFFC0_0000 == 0x6440_0000 { return true }
        }
        return false
    }
}

#if canImport(Network)
import Network
#endif

/// Connectivity flips (round 3 #6, 2026-10-22): when the path comes back,
/// RootView flushes the offline outbox and reloads, so writes made on a plane
/// land without the user doing anything.
@MainActor
final class NetWatch: ObservableObject {
    /// One monitor for the app; stores read `online` to skip a doomed request
    /// and queue at once (a request on a dead path can hang until timeout,
    /// and a timeout is never queued — 2026-10-22 offline-add bug).
    static let shared = NetWatch()
    @Published private(set) var online = true
    #if canImport(Network)
    private let monitor = NWPathMonitor()
    #endif
    init() {
        #if canImport(Network)
        monitor.pathUpdateHandler = { [weak self] path in
            let up = path.status == .satisfied
            Task { @MainActor in if self?.online != up { self?.online = up } }
        }
        monitor.start(queue: DispatchQueue(label: "netwatch"))
        #endif
    }
}
