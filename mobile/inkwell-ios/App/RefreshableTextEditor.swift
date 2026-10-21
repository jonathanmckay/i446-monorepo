import SwiftUI

/// A text editor you can pull down to refresh. SwiftUI's TextEditor has no
/// refreshable, and wrapping it in a ScrollView breaks caret auto-scroll
/// under the keyboard, so this is a UITextView (itself a UIScrollView) with
/// a UIRefreshControl. Falls back to TextEditor on macOS for the typecheck.
#if canImport(UIKit)
import UIKit

struct RefreshableTextEditor: UIViewRepresentable {
    @Binding var text: String
    var onRefresh: () async -> Void

    func makeUIView(context: Context) -> UITextView {
        let tv = UITextView()
        tv.delegate = context.coordinator
        tv.backgroundColor = .clear
        let size = UIFont.preferredFont(forTextStyle: .body).pointSize
        tv.font = UIFontMetrics(forTextStyle: .body).scaledFont(for: .monospacedSystemFont(ofSize: size, weight: .regular))
        tv.adjustsFontForContentSizeCategory = true
        tv.textColor = UIColor(Theme.fg)
        tv.tintColor = UIColor(Theme.accent)
        tv.alwaysBounceVertical = true          // the pull works even on a short note
        tv.keyboardDismissMode = .interactive
        tv.textContainerInset = UIEdgeInsets(top: 8, left: 4, bottom: 8, right: 4)
        let rc = UIRefreshControl()
        rc.tintColor = UIColor(Theme.accent)
        rc.addTarget(context.coordinator, action: #selector(Coordinator.pulled(_:)), for: .valueChanged)
        tv.refreshControl = rc
        tv.text = text
        return tv
    }

    func updateUIView(_ tv: UITextView, context: Context) {
        context.coordinator.parent = self
        if tv.text != text { tv.text = text }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: RefreshableTextEditor
        init(_ parent: RefreshableTextEditor) { self.parent = parent }
        func textViewDidChange(_ tv: UITextView) { parent.text = tv.text }
        @objc func pulled(_ rc: UIRefreshControl) {
            Task { @MainActor in
                await parent.onRefresh()
                rc.endRefreshing()
            }
        }
    }
}
#else
struct RefreshableTextEditor: View {
    @Binding var text: String
    var onRefresh: () async -> Void
    var body: some View { TextEditor(text: $text) }
}
#endif
