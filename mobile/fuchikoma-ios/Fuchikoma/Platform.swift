import Foundation
import FuchikomaCore
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
        case .success: UINotificationFeedbackGenerator().notificationOccurred(.success)
        case .failure: UINotificationFeedbackGenerator().notificationOccurred(.error)
        }
        #endif
    }
    enum Haptic { case commit, success, failure }
}

/// Phone beacon (lib/where.py on Ix): this phone's zone decides "today"
/// everywhere. The web page sends it on load and on visibilitychange; the
/// Android client never did, which lets travel-timezone "today" drift.
enum WhereBeacon {
    static func send() {
        Task.detached(priority: .utility) {
            _ = await Api().post(FuchikomaConfig.dtdBase + "/api/where", ["tz": TimeZone.current.identifier])
        }
    }
}
