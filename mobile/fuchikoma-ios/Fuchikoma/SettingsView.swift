import SwiftUI
import FuchikomaCore

/// Endpoints (long-press any header). Persisted in UserDefaults, so a moved
/// service never needs a rebuild. Saved → both stores reload.
struct SettingsView: View {
    var onSave: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var dtd = FuchikomaConfig.dtdBase
    @State private var janus = FuchikomaConfig.janusBase
    @State private var dash = FuchikomaConfig.dashboardURL

    var body: some View {
        NavigationStack {
            Form {
                Section("dtd base") { field($dtd) }
                Section("janus base") { field($janus) }
                Section("dashboard url") { field($dash) }
                Section {
                    Button("defaults") {
                        FuchikomaConfig.resetToDefaults()
                        dtd = FuchikomaConfig.dtdBase; janus = FuchikomaConfig.janusBase; dash = FuchikomaConfig.dashboardURL
                    }
                } footer: {
                    Text("http://100.x = direct over Tailscale. https://ix.tail9c51d5.ts.net:8560 / :8561 = via tailscale serve.")
                }
            }
            .navigationTitle("endpoints")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("save") {
                        FuchikomaConfig.dtdBase = dtd
                        FuchikomaConfig.janusBase = janus
                        FuchikomaConfig.dashboardURL = dash
                        onSave(); dismiss()
                    }
                }
            }
        }
    }

    private func field(_ b: Binding<String>) -> some View {
        TextField("url", text: b)
            .font(Theme.monoSmall)
            .autocorrectionDisabled()
            #if os(iOS)
            .textInputAutocapitalization(.never)
            .keyboardType(.URL)
            #endif
    }
}
