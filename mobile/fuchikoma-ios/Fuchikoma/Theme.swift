import SwiftUI

enum Theme {
    static let bg = Color(red: 0.04, green: 0.04, blue: 0.06)
    static let fg = Color(red: 0.90, green: 0.90, blue: 0.88)
    static let dim = Color(red: 0.50, green: 0.50, blue: 0.52)
    static let accent = Color(red: 0.76, green: 0.99, blue: 0.05)   // g245 lime, the 0分 color
    static let done = Color(red: 0.13, green: 0.62, blue: 0.33)
    static let edit = Color(red: 0.49, green: 0.33, blue: 0.84)
    static let mono = Font.system(.body, design: .monospaced)
    static let monoSmall = Font.system(.caption, design: .monospaced)
}

extension Color {
    /// "#rrggbb" (what the Flask payloads carry) → Color; anything else → fallback.
    init(hex: String, fallback: Color = Theme.fg) {
        var s = hex.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { self = fallback; return }
        self.init(red: Double((v >> 16) & 0xff) / 255, green: Double((v >> 8) & 0xff) / 255,
                  blue: Double(v & 0xff) / 255)
    }
}
