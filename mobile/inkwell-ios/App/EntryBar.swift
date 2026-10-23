import SwiftUI

/// Thumb-reach entry (2026-10-20: "entry should be lower right hand"): a text
/// field with a + on its right, pinned to the bottom safe area. `onPlus` runs
/// when + is tapped with an empty field (open a sheet); `onSubmit` gets the
/// text otherwise (and on the keyboard's return).
struct EntryBar: View {
    let placeholder: String
    @Binding var text: String
    var focus: FocusState<Bool>.Binding
    var disabled: Bool = false
    var multiline: Bool = false        // notes: return inserts a newline, + sends
    var codingKeys: Bool = true        // iOS keyboard accessory row of shorthand symbols
    var onSubmit: (String) -> Void
    var onPlus: () -> Void

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(placeholder, text: $text, axis: multiline ? .vertical : .horizontal)
                .lineLimit(multiline ? 1...6 : 1...1)
                .font(Theme.mono).foregroundStyle(Theme.fg)
                .focused(focus)
                .autocorrectionDisabled(!multiline)
                #if os(iOS)
                .textInputAutocapitalization(multiline ? .sentences : .never)
                .submitLabel(multiline ? .return : .go)
                .toolbar { if codingKeys { codingKeyRow } }
                #endif
                .onSubmit { if !multiline { submit() } }
                .padding(.horizontal, 12).padding(.vertical, 9)
                .background(Color(white: 0.1)).clipShape(RoundedRectangle(cornerRadius: 10))
            Button {
                if text.trimmingCharacters(in: .whitespaces).isEmpty { onPlus() } else { submit() }
            } label: {
                Image(systemName: "plus").font(.title2.bold())
                    .frame(width: 44, height: 44)
                    .background(Theme.accent).foregroundStyle(.black)
                    .clipShape(Circle())
            }
            .buttonStyle(.plain)
            .disabled(disabled)
        }
        .padding(.horizontal, 14).padding(.top, 6).padding(.bottom, 8)
        .background(Theme.bg.opacity(0.96))
    }

    private func submit() {
        let t = text.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        text = ""
        onSubmit(t)
    }

    #if os(iOS)
    /// "Coding keys": a scrollable accessory row above the keyboard with the
    /// symbols JM's task shorthand needs — `(N)` time, `[N]` points, `{N}` 0g
    /// points, `@code` domain, `#label`, `-` for time ranges (1400-1415) and
    /// `-1g`-style labels, and the fullwidth `，` used as a batch separator —
    /// followed by the digits. SwiftUI's TextField exposes no caret position,
    /// so each key appends to the end of the field. That is exactly the
    /// left-to-right flow for these commands: tap `(`, type `30`, tap `)` →
    /// `(30)`. (Mid-string edits still need the on-screen keyboard.)
    private var codingKeyRow: some ToolbarContent {
        ToolbarItem(placement: .keyboard) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    // Hide-keyboard key (2026-10-22 dtd note #1: "add hide
                    // keyboard option"), first so it sits under the thumb.
                    Button { focus.wrappedValue = false } label: {
                        Image(systemName: "keyboard.chevron.compact.down")
                            .foregroundStyle(Theme.accent)
                            .frame(minWidth: 36, minHeight: 34)
                            .background(Color(white: 0.16))
                            .clipShape(RoundedRectangle(cornerRadius: 7))
                    }
                    .buttonStyle(.plain)
                    ForEach(Self.keys, id: \.self) { k in
                        Button { insert(k) } label: {
                            Text(k)
                                .font(Theme.mono)
                                .foregroundStyle(Theme.fg)
                                .frame(minWidth: 30, minHeight: 34)
                                .background(Color(white: 0.16))
                                .clipShape(RoundedRectangle(cornerRadius: 7))
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 2)
            }
            .frame(maxWidth: .infinity)
        }
    }

    /// Symbols first (the high-value, hard-to-reach ones), then digits.
    private static let keys = ["(", ")", "[", "]", "{", "}", "@", "#", "-", "，",
                               "1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]

    private func insert(_ s: String) {
        text.append(s)
        Platform.haptic(.key)
    }
    #endif
}
