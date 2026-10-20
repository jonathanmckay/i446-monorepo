import SwiftUI
#if APP_INKWELL
import VaultCore
#endif

@main
struct InkwellApp: App {
    init() {
        #if APP_INKWELL
        // First launch: seed the vault token from the build-time secret so the
        // vault syncs without typing anything on the phone (2026-10-20).
        if VaultConfig.token.isEmpty, !VaultSecrets.token.isEmpty { VaultConfig.token = VaultSecrets.token }
        #endif
    }
    var body: some Scene {
        WindowGroup { RootView() }
    }
}
