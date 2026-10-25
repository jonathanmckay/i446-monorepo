import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// The one "coding keys" row (2026-10-25: "janus and inkwell extra keys should
/// be the same, i like janus format better"). dtd's entry bar, janus's command
/// bar, the dtd/janus edit sheets and Inkwell's note editor all draw THIS
/// view, so the key set and look can't drift apart again. Janus's format: a
/// hide-keyboard key first (under the thumb), then small mono pills — the
/// symbols JM's shorthand needs (`(N)` time, `[N]` points, `{N}` 0g points,
/// `@code` domain, `#label`, `-` for 1400-1415 ranges and `-1g` labels, the
/// fullwidth `，` batch separator), then the digits. Inkwell adds two markdown
/// pills in the same style (`[[` wikilink, `- [ ]` checkbox).
///
/// Keys insert AT THE CARET of whatever field is first responder (UIKeyInput
/// `insertText`, which the field reports as a normal edit, so SwiftUI
/// bindings and UITextView delegates stay in sync). That replaces the old
/// append-to-end EntryBar behavior and works in multi-field Forms without
/// any per-field wiring.
struct KeyRow: View {
    var markdown: Bool = false
    /// Hide the keyboard. Default resigns the first responder; EntryBar
    /// passes its FocusState so SwiftUI's focus stays truthful.
    var onHide: (() -> Void)? = nil
    /// Inside the system keyboard toolbar (sheets): no background of our own.
    /// iOS already draws the toolbar as a grey rounded box, and our full-width
    /// dark fill showed as a black rectangle spilling outside it (2026-10-25:
    /// "renders weird (black rectangle outside the grey box with rounded
    /// corners)"). The entry bars keep the fill; they sit on the app's canvas.
    var bare: Bool = false

    struct Key: Hashable {
        let label: String
        let text: String
        var back: Int = 0          // caret steps back after insert (pairs park inside)
    }

    static let symbols: [Key] = ["(", ")", "[", "]", "{", "}", "@", "#", "-", "，"].map { Key(label: $0, text: $0) }
    static let markdownKeys: [Key] = [Key(label: "[[", text: "[[]]", back: 2), Key(label: "- [ ]", text: "- [ ] ")]
    static let digits: [Key] = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"].map { Key(label: $0, text: $0) }

    /// Symbols first (the high-value, hard-to-reach ones), markdown extras
    /// (Inkwell only), then digits.
    var keys: [Key] { Self.symbols + (markdown ? Self.markdownKeys : []) + Self.digits }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                // Hide-keyboard key (2026-10-22 dtd note #1: "add hide
                // keyboard option"), first so it sits under the thumb.
                Button { if let onHide { onHide() } else { Self.hideKeyboard() } } label: {
                    Image(systemName: "keyboard.chevron.compact.down")
                        .foregroundStyle(Theme.accent)
                        .frame(minWidth: 36, minHeight: 34)
                        .background(Color(white: 0.16))
                        .clipShape(RoundedRectangle(cornerRadius: 7))
                }
                .buttonStyle(.plain)
                ForEach(keys, id: \.self) { k in
                    Button { Self.insert(k) } label: {
                        Text(k.label)
                            .font(Theme.mono)
                            .foregroundStyle(Theme.fg)
                            .padding(.horizontal, k.label.count > 1 ? 6 : 0)
                            .frame(minWidth: 30, minHeight: 34)
                            .background(Color(white: 0.16))
                            .clipShape(RoundedRectangle(cornerRadius: 7))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, bare ? 2 : 8).padding(.vertical, bare ? 0 : 4)
        }
        .frame(maxWidth: .infinity)
        .background(bare ? Color.clear : Theme.bg.opacity(0.96))
    }

    #if canImport(UIKit)
    static func insert(_ k: Key) {
        guard let field = firstResponder() as? (UIResponder & UITextInput) else { return }
        field.insertText(k.text)
        if k.back > 0, let r = field.selectedTextRange,
           let p = field.position(from: r.start, offset: -k.back) {
            field.selectedTextRange = field.textRange(from: p, to: p)
        }
        Platform.haptic(.key)
    }

    static func hideKeyboard() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }

    /// UIKit has no public "current first responder"; a nil-targeted action
    /// is delivered to it, so it reports itself.
    static func firstResponder() -> UIResponder? {
        FirstResponderProbe.found = nil
        UIApplication.shared.sendAction(#selector(UIResponder.keyRowReportFirstResponder(_:)), to: nil, from: nil, for: nil)
        return FirstResponderProbe.found
    }
    #else
    static func insert(_ k: Key) {}
    static func hideKeyboard() {}
    #endif
}

#if canImport(UIKit)
private enum FirstResponderProbe { static weak var found: UIResponder? }
extension UIResponder {
    @objc fileprivate func keyRowReportFirstResponder(_ sender: Any?) { FirstResponderProbe.found = self }
}

/// Inkwell's UITextView takes the same SwiftUI row as its inputAccessoryView,
/// hosted, so it rides the keyboard (and interactive dismiss) natively.
final class KeyRowAccessory: UIView {
    private let host: UIHostingController<KeyRow>
    init(markdown: Bool) {
        host = UIHostingController(rootView: KeyRow(markdown: markdown))
        super.init(frame: CGRect(x: 0, y: 0, width: 320, height: 46))
        autoresizingMask = .flexibleWidth
        host.view.backgroundColor = .clear
        if #available(iOS 16.4, *) { host.safeAreaRegions = [] }
        backgroundColor = UIColor(Theme.bg)
        host.view.translatesAutoresizingMaskIntoConstraints = false
        addSubview(host.view)
        NSLayoutConstraint.activate([
            host.view.leadingAnchor.constraint(equalTo: leadingAnchor),
            host.view.trailingAnchor.constraint(equalTo: trailingAnchor),
            host.view.topAnchor.constraint(equalTo: topAnchor),
            host.view.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override var intrinsicContentSize: CGSize { CGSize(width: UIView.noIntrinsicMetric, height: 46) }
}
#endif

extension View {
    /// The key row for sheets (dtd edit/split/form, janus fill/edit): a
    /// keyboard toolbar item. Inside a sheet's NavigationStack it becomes the
    /// keyboard's accessory, so Form keyboard avoidance counts its height and
    /// scrolls the focused field above it. (The bottom entry bars do NOT use
    /// this; see EntryBar for why.)
    func keyRowToolbar() -> some View {
        #if os(iOS)
        return self.toolbar { ToolbarItem(placement: .keyboard) { KeyRow(bare: true) } }
        #else
        return self
        #endif
    }
}
