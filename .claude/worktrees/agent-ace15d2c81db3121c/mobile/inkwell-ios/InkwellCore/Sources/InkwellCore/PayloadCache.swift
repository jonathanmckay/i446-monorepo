import Foundation

/// Last good payload per screen, so a cold launch paints the previous state
/// at once (2026-10-19: "needs to open immediately") and the network only
/// has to deliver the diff. Small JSON blobs in UserDefaults.
public enum PayloadCache {
    public static func save(_ key: String, _ json: JSON) {
        guard let d = json.dict, let data = try? JSONSerialization.data(withJSONObject: d) else { return }
        UserDefaults.standard.set(data, forKey: "cache." + key)
        UserDefaults.standard.set(Date().timeIntervalSince1970, forKey: "cache." + key + ".at")
    }
    public static func load(_ key: String) -> (JSON, Date)? {
        guard let data = UserDefaults.standard.data(forKey: "cache." + key), let j = JSON.parse(data) else { return nil }
        let at = Date(timeIntervalSince1970: UserDefaults.standard.double(forKey: "cache." + key + ".at"))
        return (j, at)
    }
    public static func clock(_ d: Date) -> String {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f.string(from: d)
    }
}
