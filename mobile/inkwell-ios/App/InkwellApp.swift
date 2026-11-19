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
        // Seeded again whenever a rebuild carries a different secret (token
        // rotation, 2026-11-19), so a rotated token never needs re-pasting; a
        // token typed in settings survives rebuilds that keep the same secret.
        let seededKey = "vault.seededSecret"
        if !VaultSecrets.token.isEmpty,
           VaultConfig.token.isEmpty || UserDefaults.standard.string(forKey: seededKey) != VaultSecrets.token {
            VaultConfig.token = VaultSecrets.token
        }
        UserDefaults.standard.set(VaultSecrets.token, forKey: seededKey)
        if !VaultConfig.token.isEmpty { InkwellConfig.bearer = VaultConfig.token }   // rotated in-app
        #endif
        Theme.applyUIKitMonospace()
    }
    var body: some Scene {
        WindowGroup { RootView() }
    }
}
