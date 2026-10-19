import SwiftUI
import InkwellCore
#if APP_INKWELL
import VaultCore
#endif

/// Endpoints (long-press any header). Persisted in UserDefaults, so a moved
/// service never needs a rebuild. Saved → both stores reload.
struct SettingsView: View {
    var onSave: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var dtd = InkwellConfig.dtdBase
    @State private var janus = InkwellConfig.janusBase
    @State private var dash = InkwellConfig.dashboardURL
    #if APP_INKWELL
    @State private var vaultBase = VaultConfig.base
    @State private var vaultToken = VaultConfig.token
    #endif

    var body: some View {
        NavigationStack {
            Form {
                Section("dtd base") { field($dtd) }
                Section("janus base") { field($janus) }
                Section("dashboard url") { field($dash) }
                #if APP_INKWELL
                Section("vault base") { field($vaultBase) }
                Section {
                    SecureField("bearer token", text: $vaultToken).font(Theme.monoSmall)
                } header: { Text("vault token") } footer: {
                    Text("mint on ix: python3 tools/vault-mobile/server.py token <device>")
                }
                #endif
                Section {
                    Button("defaults") {
                        InkwellConfig.resetToDefaults()
                        dtd = InkwellConfig.dtdBase; janus = InkwellConfig.janusBase; dash = InkwellConfig.dashboardURL
                    }
                } footer: {
                    Text("http://100.x = direct over Tailscale. https://ix.tail9c51d5.ts.net:8560 / :8561 = via tailscale serve. Vault default is the Funnel (:8443/vault), so it works with the VPN off.")
                }
            }
            .navigationTitle("endpoints")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("save") {
                        InkwellConfig.dtdBase = dtd
                        InkwellConfig.janusBase = janus
                        InkwellConfig.dashboardURL = dash
                        #if APP_INKWELL
                        VaultConfig.base = vaultBase
                        VaultConfig.token = vaultToken
                        VaultTabStore.shared.runSync(force: true)
                        #endif
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
