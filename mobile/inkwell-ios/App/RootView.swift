import SwiftUI
import InkwellCore

/// One source tree, three apps (XcodeGen targets, 2026-10-19):
///   dtd      → APP_DTD      task list only
///   janus    → APP_JANUS    timeline only
///   Inkwell  → (default)    vault + dashboard
/// The surface is chosen at compile time via SWIFT_ACTIVE_COMPILATION_CONDITIONS,
/// so each app ships only its own screen while sharing stores, settings, toast.
struct RootView: View {
    @ObservedObject private var toaster = Toaster.shared
    @State private var showSettings = false
    @Environment(\.scenePhase) private var phase
    @ObservedObject private var net = NetWatch.shared

    #if APP_DTD
    @StateObject private var dtd = DtdStore()
    #elseif APP_JANUS
    @StateObject private var janus = JanusStore()
    #else
    enum Tab: Hashable { case vault, dash }
    @State private var tab: Tab = .vault
    @ObservedObject private var vault = VaultTabStore.shared
    #endif

    var body: some View {
        surface
            .overlay(alignment: .bottom) { ToastView(toaster: toaster).padding(.bottom, bottomPad) }
            .sheet(isPresented: $showSettings) { SettingsView { reloadAll() } }
            .onChange(of: phase) { _, p in
                // Foreground: tell Ix which zone this phone is in (decides "today"
                // everywhere, like the web page's visibilitychange beacon), then
                // refresh the screen if it has gone stale or never loaded.
                if p == .active { WhereBeacon.send(); refreshAll() }
            }
            .onChange(of: net.online) { _, up in if up { cameOnline() } }
            // Every SwiftUI Text/Button/Label/field inherits the monospaced
            // design (2026-10-22: "all text should be monospaced font,
            // buttons etc"); UIKit chrome is set in Theme.applyUIKitMonospace.
            .fontDesign(.monospaced)
            .preferredColorScheme(.dark)
            .tint(Theme.accent)
    }

    #if APP_DTD
    private var surface: some View { DtdView(store: dtd, onSettings: { showSettings = true }) }
    private var bottomPad: CGFloat { 16 }
    private func reloadAll() { dtd.reload() }
    private func refreshAll() { dtd.refreshIfStale(); Task { await dtd.flushOutbox() } }
    private func cameOnline() { Task { await dtd.load(refresh: false) } }
    #elseif APP_JANUS
    private var surface: some View { JanusView(store: janus, onSettings: { showSettings = true }) }
    private var bottomPad: CGFloat { 16 }
    private func reloadAll() { janus.reload() }
    private func refreshAll() { janus.refreshIfStale(); Task { await janus.flushOutbox() } }
    private func cameOnline() { Task { await janus.load() } }
    #else
    private var surface: some View {
        TabView(selection: $tab) {
            VaultView(store: vault, onSettings: { showSettings = true })
                .tabItem { Label("vault", systemImage: "books.vertical") }
                .tag(Tab.vault)
            DashboardView(onSettings: { showSettings = true })
                .tabItem { Label("dash", systemImage: "chart.bar") }
                .tag(Tab.dash)
        }
    }
    private var bottomPad: CGFloat { 56 }
    private func reloadAll() { vault.fetchLanding(VaultView.landing); vault.runSync(force: true) }
    private func refreshAll() { vault.fetchLanding(VaultView.landing); vault.syncIfStale() }
    private func cameOnline() { vault.syncIfStale() }
    #endif
}
