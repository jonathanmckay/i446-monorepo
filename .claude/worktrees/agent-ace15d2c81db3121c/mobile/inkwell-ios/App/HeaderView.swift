import SwiftUI

/// Two lines, like the Android header: the tab's 分 line, then the endpoint
/// or the last error — a wedged tailnet must not look like an empty day.
/// Long-press → endpoint settings. Optional trailing action (add / reload).
struct HeaderView: View {
    let title: String
    let status: String
    var onLongPress: () -> Void
    var actionIcon: String? = nil
    var onAction: (() -> Void)? = nil
    var onRetry: (() -> Void)? = nil     // tap on a ⚠ status line

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(Theme.mono).foregroundStyle(Theme.fg).lineLimit(1)
                Text(status.hasPrefix("⚠") && onRetry != nil ? status + "  · tap to retry" : status)
                    .font(Theme.monoSmall)
                    .foregroundStyle(status.hasPrefix("⚠") ? Color.orange : Theme.dim)
                    .lineLimit(1).truncationMode(.middle)
                    .onTapGesture { if status.hasPrefix("⚠") { onRetry?() } }
            }
            Spacer()
            if let icon = actionIcon, let act = onAction {
                Button(action: act) { Image(systemName: icon).font(.title3) }
                    .buttonStyle(.plain).foregroundStyle(Theme.accent)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(Theme.bg)
        .contentShape(Rectangle())
        .onLongPressGesture(minimumDuration: 0.6) { onLongPress() }
    }
}
