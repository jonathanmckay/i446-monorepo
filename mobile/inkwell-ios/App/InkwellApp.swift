import SwiftUI
import InkwellCore
#if APP_INKWELL
import VaultCore
#endif

@main
struct InkwellApp: App {
    init() {
        // The dtd Funnel route wants this device's token (same one as the vault).
        InkwellConfig.bearer = VaultSecrets.token
        #if APP_INKWELL
        // First launch: seed the vault token from the build-time secret so the
        // vault syncs without typing anything on the phone (2026-10-20).
        if VaultConfig.token.isEmpty, !VaultSecrets.token.isEmpty { VaultConfig.token = VaultSecrets.token }
        if !VaultConfig.token.isEmpty { InkwellConfig.bearer = VaultConfig.token }   // rotated in-app
        #endif
        Theme.applyUIKitMonospace()
    }
    var body: some Scene {
        WindowGroup { RootView() }
    }
}
