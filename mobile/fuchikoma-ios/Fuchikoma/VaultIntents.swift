#if canImport(AppIntents)
import AppIntents
import VaultCore

/// "Capture to vault" for the Action Button, Siri, Shortcuts and the lock
/// screen. Writes straight into the local outbox, so it works with no
/// network and without opening the app; the next foreground sync (or the
/// best-effort push below) drops it into z_ibx/phone/ on ix.
@available(iOS 17, macOS 14, *)
struct CaptureToVaultIntent: AppIntent {
    static var title: LocalizedStringResource = "Capture to vault"
    static var description = IntentDescription("Save a new note into the vault inbox. Works offline.")
    static var openAppWhenRun = false

    @Parameter(title: "Note", requestValueDialog: "What do you want to capture?")
    var text: String

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let store = VaultTabStore.shared
        guard store.capture(text) else { return .result(dialog: "Nothing to capture.") }
        // Give the push a moment; if it fails the note stays queued and the
        // header shows "1 pending" next time the tab opens.
        try? await Task.sleep(nanoseconds: 1_500_000_000)
        let pending = store.store.pendingCount
        return .result(dialog: pending == 0 ? "Captured." : "Captured (will sync).")
    }
}

@available(iOS 17, macOS 14, *)
struct VaultShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: CaptureToVaultIntent(),
            phrases: ["Capture to \(.applicationName)", "Note in \(.applicationName)", "\(.applicationName) capture"],
            shortTitle: "Capture",
            systemImageName: "square.and.pencil"
        )
    }
}
#endif
