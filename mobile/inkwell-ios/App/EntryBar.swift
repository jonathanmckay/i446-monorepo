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
}
