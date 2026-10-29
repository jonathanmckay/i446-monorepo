import SwiftUI

/// A text editor you can pull down to refresh. SwiftUI's TextEditor has no
/// refreshable, and wrapping it in a ScrollView breaks caret auto-scroll
/// under the keyboard, so this is a UITextView (itself a UIScrollView) with
/// a UIRefreshControl. Falls back to TextEditor on macOS for the typecheck.
#if canImport(UIKit)
import UIKit
import InkwellCore

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
        tv.contentInsetAdjustmentBehavior = .never
        tv.keyboardDismissMode = .interactive
        tv.textContainerInset = UIEdgeInsets(top: 8, left: 4, bottom: 8, right: 4)
        let rc = UIRefreshControl()
        rc.tintColor = UIColor(Theme.accent)
        rc.attributedTitle = NSAttributedString(string: "refreshing new-notes",
            attributes: [.foregroundColor: UIColor(Theme.accent), .font: UIFont.monospacedSystemFont(ofSize: 11, weight: .regular)])
        rc.addTarget(context.coordinator, action: #selector(Coordinator.pulled(_:)), for: .valueChanged)
        tv.refreshControl = rc
        // Coding keys = the shared KeyRow (2026-10-25: "janus and inkwell
        // extra keys should be the same, i like janus format better"), hosted
        // as the accessory so it rides the keyboard and interactive dismiss.
        // Replaces Inkwell's own UIKit row (round 3 #7, 2026-10-22); `[[` and
        // `- [ ]` survive as the markdown extras, inserted at the caret.
        tv.inputAccessoryView = KeyRowAccessory(markdown: true)
        tv.text = text
        Self.restyle(tv)
        return tv
    }

    /// Live heading/table styling, Obsidian Live Preview style (2026-10-22):
    /// `#` lines grow h1 > h2 > h3 > h4 > body with the hashes dimmed, and a
    /// table's pipes and separator row dim so the cells read. The text itself
    /// is never touched; only attributes, so undo, saving and the sha merge
    /// see the same plain markdown. Skipped mid-IME composition (pinyin):
    /// rewriting attributes under marked text cancels it. `lines` limits the
    /// pass to the paragraphs touching that range: while typing only the
    /// edited lines are restyled, because re-laying out the whole note on
    /// every keystroke jumped the view to another line (2026-10-29).
    static func restyle(_ tv: UITextView, lines: NSRange? = nil) {
        guard tv.markedTextRange == nil else { return }
        let size = UIFont.preferredFont(forTextStyle: .body).pointSize
        func mono(_ scale: CGFloat, _ weight: UIFont.Weight) -> UIFont {
            UIFontMetrics(forTextStyle: .body).scaledFont(for: .monospacedSystemFont(ofSize: (size * scale).rounded(), weight: weight))
        }
        let fg = UIColor(Theme.fg), dim = UIColor(Theme.dim)
        let base: [NSAttributedString.Key: Any] = [.font: mono(1, .regular), .foregroundColor: fg]
        let ts = tv.textStorage, ns = ts.string as NSString
        let selection = tv.selectedRange
        var span = NSRange(location: 0, length: ns.length)
        if let lines {
            let loc = min(lines.location, ns.length)
            span = ns.paragraphRange(for: NSRange(location: loc, length: min(lines.length, ns.length - loc)))
        }
        ts.beginEditing()
        ts.setAttributes(base, range: span)
        ns.enumerateSubstrings(in: span, options: .byLines) { line, r, _, _ in
            guard let line else { return }
            if let level = MarkdownLine.headingLevel(line) {
                // h1 28 · h2 22 · h3 20 · h4+ bold body, matching MarkdownView
                ts.addAttribute(.font, value: mono([1.65, 1.3, 1.18][safe: level - 1] ?? 1, .bold), range: r)
                let hashes = ns.range(of: String(repeating: "#", count: level), range: r)
                if hashes.location != NSNotFound { ts.addAttribute(.foregroundColor, value: dim, range: hashes) }
            } else if MarkdownLine.isTableSeparator(line) {
                ts.addAttribute(.foregroundColor, value: dim, range: r)
            } else if MarkdownLine.isTableRow(line) {
                var i = r.location
                while i < NSMaxRange(r) {
                    if ns.character(at: i) == 0x7C { ts.addAttribute(.foregroundColor, value: dim, range: NSRange(location: i, length: 1)) }
                    i += 1
                }
            }
        }
        ts.endEditing()
        if tv.selectedRange != selection { tv.selectedRange = selection }
        tv.typingAttributes = base   // the next keystroke restyles its own line
    }

    func updateUIView(_ tv: UITextView, context: Context) {
        context.coordinator.parent = self
        if tv.text != text { Self.replace(tv, with: text) }
    }

    /// Adopt text that changed outside the editor (a sync or a merged put)
    /// without moving the reader: assigning `tv.text` puts the caret at the
    /// end and scrolls there. The caret keeps its place, shifted by the
    /// length change when the edit landed above it, and the scroll offset
    /// stays put (2026-10-29: "the window refocuses to a different line").
    static func replace(_ tv: UITextView, with text: String) {
        let old = tv.text as NSString, new = text as NSString
        let sel = tv.selectedRange, offset = tv.contentOffset
        var prefix = 0
        let n = min(old.length, new.length)
        while prefix < n && old.character(at: prefix) == new.character(at: prefix) { prefix += 1 }
        let caret = sel.location <= prefix ? sel.location : sel.location + new.length - old.length
        tv.text = text
        restyle(tv)
        tv.selectedRange = NSRange(location: max(0, min(caret, new.length)), length: 0)
        tv.layoutIfNeeded()
        let maxY = max(0, tv.contentSize.height - tv.bounds.height + tv.adjustedContentInset.bottom)
        tv.setContentOffset(CGPoint(x: offset.x, y: min(max(offset.y, -tv.adjustedContentInset.top), maxY)), animated: false)
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: RefreshableTextEditor
        init(_ parent: RefreshableTextEditor) { self.parent = parent }
        /// Union of the ranges edited since the last restyle (several while an
        /// IME composes), in post-edit coordinates.
        private var edited: NSRange? = nil
        func textViewDidChange(_ tv: UITextView) {
            if tv.markedTextRange == nil {
                RefreshableTextEditor.restyle(tv, lines: edited ?? tv.selectedRange)
                edited = nil
            }
            parent.text = tv.text
        }

        /// Obsidian-style list autofill: on return, continue the current list
        /// item's marker (or break out of an empty one). Falls through to a
        /// plain newline for non-list lines.
        func textView(_ tv: UITextView, shouldChangeTextIn range: NSRange, replacementText text: String) -> Bool {
            let r = NSRange(location: range.location, length: (text as NSString).length)
            edited = edited.map { NSUnionRange($0, r) } ?? r
            guard text == "\n",
                  let edit = MarkdownList.onNewline(text: tv.text as NSString, selection: range),
                  let start = tv.position(from: tv.beginningOfDocument, offset: edit.range.location),
                  let end = tv.position(from: start, offset: edit.range.length),
                  let target = tv.textRange(from: start, to: end)
            else { return true }
            tv.replace(target, withText: edit.replacement)   // keeps undo intact
            if let caret = tv.position(from: tv.beginningOfDocument, offset: edit.cursor) {
                tv.selectedTextRange = tv.textRange(from: caret, to: caret)
            }
            RefreshableTextEditor.restyle(tv, lines: NSRange(location: edit.range.location,
                                                             length: (edit.replacement as NSString).length))
            edited = nil
            parent.text = tv.text   // `replace` doesn't fire textViewDidChange
            return false
        }
        @objc func pulled(_ rc: UIRefreshControl) {
            Task { @MainActor in
                let started = Date()
                await parent.onRefresh()
                let remaining = 0.7 - Date().timeIntervalSince(started)
                if remaining > 0 { try? await Task.sleep(for: .seconds(remaining)) }
                rc.endRefreshing()
            }
        }
    }
}
private extension Array {
    subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}
#else
struct RefreshableTextEditor: View {
    @Binding var text: String
    var onRefresh: () async -> Void
    var body: some View { TextEditor(text: $text) }
}
#endif
