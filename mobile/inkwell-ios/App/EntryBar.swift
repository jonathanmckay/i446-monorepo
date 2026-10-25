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
    var codingKeys: Bool = true        // KeyRow of shorthand symbols under the field while focused
    var onSubmit: (String) -> Void
    var onPlus: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .bottom, spacing: 8) {
                TextField(placeholder, text: $text, axis: multiline ? .vertical : .horizontal)
                    .lineLimit(multiline ? 1...6 : 1...1)
                    .font(Theme.mono).foregroundStyle(Theme.fg)
                    .focused(focus)
                    .autocorrectionDisabled(!multiline)
                    #if os(iOS)
                    .textInputAutocapitalization(multiline ? .sentences : .never)
                    .submitLabel(multiline ? .return : .go)
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
            // Coding keys INLINE, under the field, while it has focus
            // (2026-10-25, janus: "extra keys covered new time entry box").
            // They used to be a `.toolbar(.keyboard)` item on the TextField,
            // but this bar lives in a `.safeAreaInset(edge: .bottom)` outside
            // any NavigationStack, where SwiftUI's keyboard avoidance doesn't
            // reliably count the toolbar's height, so on the phone the row sat
            // on top of the field being typed into. As part of
            // the inset the row is laid out, not floated: avoidance lifts
            // field + keys together and the field is always above the keys.
            #if os(iOS)
            if codingKeys && focus.wrappedValue {
                KeyRow(onHide: { focus.wrappedValue = false })
            }
            #endif
        }
        .background(Theme.bg.opacity(0.96))
    }

    private func submit() {
        let t = text.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        text = ""
        onSubmit(t)
    }
}
