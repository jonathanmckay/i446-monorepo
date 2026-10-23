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
        tv.inputAccessoryView = Self.keyRow(for: tv)
        tv.text = text
        Self.restyle(tv)
        return tv
    }

    /// Live heading/table styling, Obsidian Live Preview style (2026-10-22):
    /// `#` lines grow h1 > h2 > h3 > h4 > body with the hashes dimmed, and a
    /// table's pipes and separator row dim so the cells read. The text itself
    /// is never touched; only attributes, so undo, saving and the sha merge
    /// see the same plain markdown. Skipped mid-IME composition (pinyin):
    /// rewriting attributes under marked text cancels it.
    static func restyle(_ tv: UITextView) {
        guard tv.markedTextRange == nil else { return }
        let size = UIFont.preferredFont(forTextStyle: .body).pointSize
        func mono(_ scale: CGFloat, _ weight: UIFont.Weight) -> UIFont {
            UIFontMetrics(forTextStyle: .body).scaledFont(for: .monospacedSystemFont(ofSize: (size * scale).rounded(), weight: weight))
        }
        let fg = UIColor(Theme.fg), dim = UIColor(Theme.dim)
        let base: [NSAttributedString.Key: Any] = [.font: mono(1, .regular), .foregroundColor: fg]
        let ts = tv.textStorage, ns = ts.string as NSString
        let selection = tv.selectedRange
        ts.beginEditing()
        ts.setAttributes(base, range: NSRange(location: 0, length: ns.length))
        ns.enumerateSubstrings(in: NSRange(location: 0, length: ns.length), options: .byLines) { line, r, _, _ in
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
        tv.selectedRange = selection
        tv.typingAttributes = base   // the next keystroke restyles its own line
    }

    /// Inkwell's coding-key row (round 3 #7, 2026-10-22: "add the common
    /// brackets / chars that i use for inkwell too"): the markdown and
    /// shorthand symbols that are two keyboard pages away, inserted AT the
    /// caret (a UITextView has one, unlike dtd's SwiftUI field). Pairs like
    /// [[ ]] insert both halves and park the caret between them. ⌄ hides
    /// the keyboard.
    static let keys: [(label: String, insert: String, back: Int)] = [
        ("⌄", "", 0), ("[[ ]]", "[[]]", 2), ("[ ]", "[]", 1), ("( )", "()", 1), ("{ }", "{}", 1),
        ("#", "#", 0), ("-", "- ", 0), ("☐", "- [ ] ", 0), ("*", "*", 0), ("`", "`", 0),
        (">", "> ", 0), ("@", "@", 0), ("|", "|", 0), ("=", "=", 0), ("，", "，", 0), ("⇥", "\t", 0),
    ]

    static func keyRow(for tv: UITextView) -> UIView {
        let bar = UIView(frame: CGRect(x: 0, y: 0, width: 320, height: 44))
        bar.backgroundColor = UIColor(white: 0.08, alpha: 1)
        let scroll = UIScrollView()
        scroll.showsHorizontalScrollIndicator = false
        scroll.translatesAutoresizingMaskIntoConstraints = false
        let stack = UIStackView()
        stack.axis = .horizontal; stack.spacing = 6
        stack.translatesAutoresizingMaskIntoConstraints = false
        for k in keys {
            var cfg = UIButton.Configuration.filled()
            cfg.baseBackgroundColor = UIColor(white: 0.18, alpha: 1)
            cfg.baseForegroundColor = UIColor(Theme.fg)
            cfg.contentInsets = NSDirectionalEdgeInsets(top: 6, leading: 10, bottom: 6, trailing: 10)
            cfg.attributedTitle = AttributedString(k.label, attributes: AttributeContainer(
                [.font: UIFont.monospacedSystemFont(ofSize: 16, weight: .regular)]))
            let b = UIButton(configuration: cfg, primaryAction: UIAction { [weak tv] _ in
                guard let tv else { return }
                if k.insert.isEmpty { tv.resignFirstResponder(); return }
                tv.insertText(k.insert)          // fires textViewDidChange → binding stays in sync
                if k.back > 0, let r = tv.selectedTextRange,
                   let p = tv.position(from: r.start, offset: -k.back) {
                    tv.selectedTextRange = tv.textRange(from: p, to: p)
                }
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            })
            stack.addArrangedSubview(b)
        }
        scroll.addSubview(stack)
        bar.addSubview(scroll)
        NSLayoutConstraint.activate([
            scroll.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 6),
            scroll.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -6),
            scroll.topAnchor.constraint(equalTo: bar.topAnchor),
            scroll.bottomAnchor.constraint(equalTo: bar.bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor),
            stack.centerYAnchor.constraint(equalTo: scroll.centerYAnchor),
            stack.heightAnchor.constraint(equalToConstant: 34),
        ])
        bar.autoresizingMask = .flexibleWidth
        return bar
    }

    func updateUIView(_ tv: UITextView, context: Context) {
        context.coordinator.parent = self
        if tv.text != text { tv.text = text; Self.restyle(tv) }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: RefreshableTextEditor
        init(_ parent: RefreshableTextEditor) { self.parent = parent }
        func textViewDidChange(_ tv: UITextView) {
            RefreshableTextEditor.restyle(tv)
            parent.text = tv.text
        }

        /// Obsidian-style list autofill: on return, continue the current list
        /// item's marker (or break out of an empty one). Falls through to a
        /// plain newline for non-list lines.
        func textView(_ tv: UITextView, shouldChangeTextIn range: NSRange, replacementText text: String) -> Bool {
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
            RefreshableTextEditor.restyle(tv)
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
