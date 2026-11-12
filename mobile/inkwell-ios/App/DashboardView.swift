import SwiftUI
import InkwellCore
#if canImport(WebKit) && canImport(UIKit)
import WebKit
#endif

/// The personal dashboard stays a web page (chart-heavy, read-mostly): a
/// WKWebView on the HTTPS tailscale-serve URL. ↻ reloads.
///
/// Offline (2026-11-12: JM "jm dash should work offline"): a few seconds after
/// each live load the rendered page is saved as a web archive in Application
/// Support (HTML + Chart.js from the CDN + cover images; the page's own
/// cachedJSON shim in dashboard.py mirrors every JSON payload into
/// localStorage, which the archive shares because it is loaded with the live
/// URL as base). When the tailnet is down at launch, or a live load fails, the
/// archive is shown instead and the header's status line says so
/// ("offline · cached HH:MM" vs the URL). ↻ and a tap on a ⚠ line retry live.
struct DashboardView: View {
    var onSettings: () -> Void
    @State private var generation = 0
    @State private var status = InkwellConfig.dashboardURL

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(title: "dashboard", status: status, onLongPress: onSettings,
                       actionIcon: "arrow.clockwise", onAction: { generation += 1 },
                       onRetry: { generation += 1 })
            WebView(url: InkwellConfig.dashboardURL, forceLive: generation > 0, status: $status).id(generation)
        }
        .background(Theme.bg)
    }
}

#if canImport(WebKit) && canImport(UIKit)
struct WebView: UIViewRepresentable {
    let url: String
    /// ↻ (generation > 0) always tries the live URL, even when NetWatch says
    /// the path is down — that is what "retry" means; a failure still falls
    /// back to the archive. The first load trusts NetWatch and skips the
    /// doomed request (same reasoning as the stores' offline queue).
    let forceLive: Bool
    @Binding var status: String

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        let v = WKWebView(frame: .zero, configuration: cfg)
        v.isOpaque = false
        v.backgroundColor = .black
        v.scrollView.backgroundColor = .black
        v.navigationDelegate = context.coordinator
        // NetWatch is @MainActor; read it here (makeUIView is main-actor) and
        // hand the answer to the non-isolated coordinator.
        context.coordinator.start(v, online: NetWatch.shared.online)
        return v
    }
    func updateUIView(_ v: WKWebView, context: Context) { context.coordinator.parent = self }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var parent: WebView
        /// Set before the archive load starts, so the didFinish it produces
        /// neither re-archives the archive nor flips the status back to live.
        private var archiveMode = false
        init(_ parent: WebView) { self.parent = parent }

        static var archiveFile: URL {
            let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            return dir.appendingPathComponent("jmdash.webarchive")
        }
        private var liveURL: URL? { URL(string: parent.url) }
        private var hasArchive: Bool { FileManager.default.fileExists(atPath: Self.archiveFile.path) }

        /// Off the view-update path: start() runs inside makeUIView, and a
        /// @State write there is dropped with a runtime warning.
        private func setStatus(_ s: String) { DispatchQueue.main.async { self.parent.status = s } }

        func start(_ v: WKWebView, online: Bool) {
            guard let u = liveURL else { setStatus("⚠ bad dashboard URL: " + parent.url); return }
            if !parent.forceLive && !online && hasArchive { loadArchive(v); return }
            archiveMode = false
            // 10 s: a tailnet that is up while ix is unreachable would otherwise
            // hang the full 60 s default before didFail lets the archive in.
            v.load(URLRequest(url: u, timeoutInterval: 10))
        }

        private func loadArchive(_ v: WKWebView) {
            guard let u = liveURL, let data = try? Data(contentsOf: Self.archiveFile) else {
                setStatus("⚠ offline · no cached copy yet"); return
            }
            archiveMode = true
            // baseURL = the live URL keeps the document's origin, so the page's
            // localStorage copies of /api/* are readable from the archive.
            v.load(data, mimeType: "application/x-webarchive", characterEncodingName: "utf-8", baseURL: u)
            let saved = (try? FileManager.default.attributesOfItem(atPath: Self.archiveFile.path)[.modificationDate] as? Date) ?? Date()
            setStatus("offline · cached " + Self.stamp(saved))
        }

        private static func stamp(_ d: Date) -> String {
            let f = DateFormatter()
            f.dateFormat = Calendar.current.isDateInToday(d) ? "HH:mm" : "M/d HH:mm"
            return f.string(from: d)
        }

        /// Only the dashboard page itself is archived — not /jmreads or /more
        /// reached by a tap — and it is compared with the trailing slash
        /// ignored since /personal redirects to /personal/.
        private func isLivePage(_ u: URL?) -> Bool {
            guard let u = u, let l = liveURL else { return false }
            let slash = CharacterSet(charactersIn: "/")
            return u.host == l.host && u.path.trimmingCharacters(in: slash) == l.path.trimmingCharacters(in: slash)
        }

        func webView(_ v: WKWebView, didFinish navigation: WKNavigation!) {
            guard !archiveMode else { return }
            setStatus(parent.url)
            guard isLivePage(v.url) else { return }
            // didFinish fires at the load event; the page's fetch()es and the
            // cover <img>s they insert land afterwards, and only resources in
            // the document's cache make it into the archive — so wait a beat.
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak self, weak v] in
                guard let self = self, let v = v, !self.archiveMode, self.isLivePage(v.url) else { return }
                v.createWebArchiveData { result in
                    guard case .success(let data) = result else { return }
                    let f = Self.archiveFile
                    try? FileManager.default.createDirectory(at: f.deletingLastPathComponent(), withIntermediateDirectories: true)
                    try? data.write(to: f, options: .atomic)
                }
            }
        }

        func webView(_ v: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            failed(v, error)
        }
        func webView(_ v: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            failed(v, error)
        }
        /// Main-frame failures only (WebKit never reports the slice iframe or a
        /// fetch() here). A cancelled load is a superseded one, not an outage.
        private func failed(_ v: WKWebView, _ error: Error) {
            let e = error as NSError
            if e.domain == NSURLErrorDomain && e.code == NSURLErrorCancelled { return }
            if archiveMode { setStatus("⚠ offline · cached copy failed: " + e.localizedDescription); return }
            if hasArchive { loadArchive(v) } else { setStatus("⚠ offline · no cached copy yet (" + e.localizedDescription + ")") }
        }
    }
}
#else
struct WebView: View {   // macOS typecheck stand-in
    let url: String
    let forceLive: Bool
    @Binding var status: String
    var body: some View { Text(url) }
}
#endif
