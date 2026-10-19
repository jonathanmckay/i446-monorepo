import SwiftUI
import FuchikomaCore

/// Three surfaces, one app: dtd (tasks), janus (timeline), dashboard (web).
/// Stores live here so tab flips never refetch; each tab owns its header.
struct RootView: View {
    enum Tab: Hashable { case dtd, janus, dash }

    @State private var tab: Tab = .dtd
    @StateObject private var dtd = DtdStore()
    @StateObject private var janus = JanusStore()
    @ObservedObject private var toaster = Toaster.shared
    @State private var showSettings = false
    @Environment(\.scenePhase) private var phase

    var body: some View {
        TabView(selection: $tab) {
            DtdView(store: dtd, onSettings: { showSettings = true })
                .tabItem { Label("dtd", systemImage: "checklist") }
                .tag(Tab.dtd)
            JanusView(store: janus, onSettings: { showSettings = true })
                .tabItem { Label("janus", systemImage: "clock") }
                .tag(Tab.janus)
            DashboardView(onSettings: { showSettings = true })
                .tabItem { Label("dash", systemImage: "chart.bar") }
                .tag(Tab.dash)
        }
        .overlay(alignment: .bottom) { ToastView(toaster: toaster).padding(.bottom, 56) }
        .sheet(isPresented: $showSettings) {
            SettingsView { dtd.reload(); janus.reload() }
        }
        .onChange(of: phase) { _, p in
            // Foreground: tell Ix which zone this phone is in (decides "today"
            // everywhere, like the web page's visibilitychange beacon), then
            // refresh whatever tab is showing if it has gone stale.
            if p == .active {
                WhereBeacon.send()
                dtd.refreshIfStale()
                janus.refreshIfStale()
            }
        }
        .preferredColorScheme(.dark)
        .tint(Theme.accent)
    }
}
