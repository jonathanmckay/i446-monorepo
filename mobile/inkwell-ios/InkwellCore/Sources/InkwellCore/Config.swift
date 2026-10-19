import Foundation

/// Endpoints for the Flask services on Ix. Same reasoning as the Android
/// client: Ix's Tailscale IP, not the "ix" MagicDNS name or a LAN IP, so it
/// routes from cellular / foreign WiFi as long as Tailscale is up on the
/// phone. All three are editable in-app (long-press the header) and
/// persisted, so a moved service never needs a rebuild.
public enum InkwellConfig {
    public static let defaultDtd = "http://100.114.46.109:5560"
    public static let defaultJanus = "http://100.114.46.109:5561"
    /// The dashboard is chart-heavy and read-mostly: it stays a web page. HTTPS
    /// via `tailscale serve` (hostname, not IP — the cert is for the name).
    public static let defaultDashboard = "https://ix.tail9c51d5.ts.net/personal/"

    static let kDtd = "dtd_base", kJanus = "janus_base", kDash = "dashboard_url"

    public static var dtdBase: String {
        get { read(kDtd, defaultDtd) }
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
