import Foundation

/// Endpoints for the Flask services on Ix. Same reasoning as the Android
/// client: Ix's Tailscale IP, not the "ix" MagicDNS name or a LAN IP, so it
/// routes from cellular / foreign WiFi as long as Tailscale is up on the
/// phone. All three are editable in-app (long-press the header) and
/// persisted, so a moved service never needs a rebuild.
public enum InkwellConfig {
    /// dtd goes through the Funnel (2026-11-19): a managed VPN keeps taking the
    /// phone's VPN slot, so the app must work with Tailscale off. The Funnel
    /// route is gated by the device's bearer token (dtd.py FunnelGate).
    public static let defaultDtd = "https://ix.tail9c51d5.ts.net:8443/dtd"
    /// The default before the Funnel. A stored copy of it is treated as unset
    /// (the settings sheet saves every field, changed or not), and offline
    /// writes queued against it are replayed to the current base.
    public static let legacyDtd = "http://100.114.46.109:5560"
    public static let funnelHost = "ix.tail9c51d5.ts.net"
    public static let funnelPort = 8443

    /// The device's bearer token, set once at launch from the build secret.
    public static var bearer = ""

    /// True for a URL on Ix's public Funnel listener: reachable with the VPN
    /// off, and the only place the bearer token is ever sent.
    public static func isFunnel(_ url: URL) -> Bool {
        url.scheme?.lowercased() == "https" && url.host?.lowercased() == funnelHost && url.port == funnelPort
    }
    public static func bearer(for url: URL) -> String? {
        isFunnel(url) && !bearer.isEmpty ? bearer : nil
    }
    /// A URL saved while the old default was in force, pointed at today's base.
    public static func migrated(_ url: String) -> String {
        let base = dtdBase
        guard base != legacyDtd, url.hasPrefix(legacyDtd + "/") else { return url }
        return base + url.dropFirst(legacyDtd.count)
    }
    public static let defaultJanus = "http://100.114.46.109:5561"
    /// The dashboard is chart-heavy and read-mostly: it stays a web page. HTTPS
    /// via `tailscale serve` (hostname, not IP — the cert is for the name).
    public static let defaultDashboard = "https://ix.tail9c51d5.ts.net/personal/"

    static let kDtd = "dtd_base", kJanus = "janus_base", kDash = "dashboard_url"

    public static var dtdBase: String {
        get { let v = read(kDtd, defaultDtd); return v == legacyDtd ? defaultDtd : v }
        set { write(kDtd, newValue) }
    }
    public static var janusBase: String {
        get { read(kJanus, defaultJanus) }
        set { write(kJanus, newValue) }
    }
    public static var dashboardURL: String {
        get { read(kDash, defaultDashboard, trim: false) }   // a path prefix keeps its slash
        set { write(kDash, newValue) }
    }
    public static func resetToDefaults() {
        for k in [kDtd, kJanus, kDash] { UserDefaults.standard.removeObject(forKey: k) }
    }

    private static func read(_ key: String, _ def: String, trim: Bool = true) -> String {
        let v = UserDefaults.standard.string(forKey: key)?.trimmingCharacters(in: .whitespaces) ?? ""
        let out = v.isEmpty ? def : v
        return trim ? out.trimmingTrailingSlash() : out
    }
    private static func write(_ key: String, _ value: String) {
        let v = value.trimmingCharacters(in: .whitespaces)
        if v.isEmpty { UserDefaults.standard.removeObject(forKey: key) } else { UserDefaults.standard.set(v, forKey: key) }
    }
}

extension String {
    func trimmingTrailingSlash() -> String {
        var s = self
        while s.hasSuffix("/") && s.count > 1 && !s.hasSuffix("//") { s.removeLast() }
        return s
    }
}
