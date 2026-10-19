import SwiftUI
import FuchikomaCore
#if canImport(WebKit) && canImport(UIKit)
import WebKit
#endif

/// The personal dashboard stays a web page (chart-heavy, read-mostly): a
/// WKWebView on the HTTPS tailscale-serve URL. ↻ reloads.
struct DashboardView: View {
    var onSettings: () -> Void
    @State private var generation = 0

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(title: "dashboard", status: FuchikomaConfig.dashboardURL, onLongPress: onSettings,
                       actionIcon: "arrow.clockwise", onAction: { generation += 1 })
            WebView(url: FuchikomaConfig.dashboardURL).id(generation)
        }
        .background(Theme.bg)
    }
}

#if canImport(WebKit) && canImport(UIKit)
struct WebView: UIViewRepresentable {
    let url: String
    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        let v = WKWebView(frame: .zero, configuration: cfg)
        v.isOpaque = false
        v.backgroundColor = .black
        v.scrollView.backgroundColor = .black
        if let u = URL(string: url) { v.load(URLRequest(url: u)) }
        return v
    }
    func updateUIView(_ v: WKWebView, context: Context) {}
}
#else
struct WebView: View {   // macOS typecheck stand-in
    let url: String
    var body: some View { Text(url) }
}
#endif
