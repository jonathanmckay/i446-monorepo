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
