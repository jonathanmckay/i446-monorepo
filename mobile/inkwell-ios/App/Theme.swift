import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

enum Theme {
    static let bg = Color(red: 0.04, green: 0.04, blue: 0.06)
    static let fg = Color(red: 0.90, green: 0.90, blue: 0.88)
    static let dim = Color(red: 0.50, green: 0.50, blue: 0.52)
    static let accent = Color(red: 0.76, green: 0.99, blue: 0.05)   // g245 lime, the 0分 color
    static let done = Color(red: 0.13, green: 0.62, blue: 0.33)
    static let edit = Color(red: 0.49, green: 0.33, blue: 0.84)
    static let mono = Font.system(.body, design: .monospaced)
    static let monoSmall = Font.system(.caption, design: .monospaced)

    /// The UIKit-drawn chrome that SwiftUI's .fontDesign can't reach: nav
    /// bar titles, bar buttons (sheet cancel/save), tab bar labels, segmented
    /// pickers. System alerts and swipe-action labels stay in the system font
    /// (iOS gives no hook for those).
    static func applyUIKitMonospace() {
        #if canImport(UIKit)
        func mono(_ size: CGFloat, _ w: UIFont.Weight = .regular) -> UIFont { .monospacedSystemFont(ofSize: size, weight: w) }
        let nav = UINavigationBarAppearance()
        nav.configureWithDefaultBackground()
        nav.titleTextAttributes = [.font: mono(17, .semibold)]
        nav.largeTitleTextAttributes = [.font: mono(30, .bold)]
        let button = UIBarButtonItemAppearance()
        button.normal.titleTextAttributes = [.font: mono(17)]
        nav.buttonAppearance = button
        nav.doneButtonAppearance = button
        UINavigationBar.appearance().standardAppearance = nav
        UINavigationBar.appearance().scrollEdgeAppearance = nav
        UINavigationBar.appearance().compactAppearance = nav
        UITabBarItem.appearance().setTitleTextAttributes([.font: mono(10)], for: .normal)
        UISegmentedControl.appearance().setTitleTextAttributes([.font: mono(13)], for: .normal)
        UITextField.appearance().font = mono(17)
        #endif
    }
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
