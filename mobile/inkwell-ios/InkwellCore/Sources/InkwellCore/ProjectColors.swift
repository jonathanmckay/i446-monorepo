import Foundation

/// Project code → "#rrggbb", resolved on the phone (2026-11-12: JM "janus
/// color should be a local hashmap lookup and instantaneous"). Until now a
/// row the client invented before the server answered (the optimistic
/// pending timer, an offline local timer) scanned today's rows for a
/// same-project colour and fell back to plain fg. The static map is
/// tools/janus/mobile.py COLORS verbatim (itself a mirror of tools/dtd/dtd.py
/// / tools/did/dtd.sh); a learned overlay, filled from every parsed payload
/// and kept in UserDefaults, sits in front of it so a server-side palette
/// change wins over the copy baked into the app.
public enum ProjectColors {
    public static let defaultHex = "#bdbdbd"

    public static let builtin: [String: String] = [
        "g245": "#00e676", "h335": "#00e676", "epcn": "#00bfa5", "s897": "#1b5e20", "hcmc2": "#ffd600",
        "xk87": "#fd6c1d", "xk88": "#e65100", "hci": "#63ede0", "i9": "#2979ff",
        "n156": "#1249b4", "hcmc": "#0d3b66", "m5x2": "#d50032", "m828": "#9b0023",
        "hcb": "#f81d78",
        "hcbp": "#ff4081", "infra": "#9e9e9e", "i444": "#616161", "i447": "#a89c8a",
        // 家 = family: Pool Party teal, same as tools/tg/janus.py (was #ff4136,
        // an orange that clashed with xk87/xk88 — user report 2026-10-04).
        "hcm": "#aa00ff", "hcmp": "#7c4dff", "hcmr": "#bda6ff", "家": "#00b8d4",
        "睡觉": "#666666",
    ]

    static let learnedKey = "janus.projectColors"

    /// Server palette seen so far, project → hex. Read once per lookup from
    /// UserDefaults (a tiny dictionary; no cache to invalidate).
    public static var learned: [String: String] {
        get { UserDefaults.standard.dictionary(forKey: learnedKey) as? [String: String] ?? [:] }
        set { UserDefaults.standard.set(newValue, forKey: learnedKey) }
    }

    /// Learned overlay → static map → default grey. "" (no project) → default.
    public static func hex(for project: String) -> String {
        let p = project.trimmingCharacters(in: .whitespaces)
        guard !p.isEmpty else { return defaultHex }
        return learned[p] ?? builtin[p] ?? defaultHex
    }

    /// Remember every project → colour pair a payload carried, so the next
    /// client-invented row for that project uses what the server now sends.
    /// Only writes when something actually changed (UserDefaults churn).
    public static func learn(from rows: [TimelineRow]) {
        var l = learned
        var changed = false
        for r in rows where !r.project.isEmpty && isHex(r.color) {
            let p = r.project.trimmingCharacters(in: .whitespaces)
            if l[p] != r.color { l[p] = r.color; changed = true }
        }
        if changed { learned = l }
    }

    public static func forgetLearned() { UserDefaults.standard.removeObject(forKey: learnedKey) }

    static func isHex(_ s: String) -> Bool {
        s.count == 7 && s.hasPrefix("#") && UInt32(s.dropFirst(), radix: 16) != nil
    }
}
