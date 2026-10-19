import SwiftUI

/// Bottom toast. A failure keeps its retry button until tapped or replaced —
/// a vanished row plus a 2-second toast is invisible on a phone.
@MainActor
final class Toaster: ObservableObject {
    static let shared = Toaster()
    @Published var message: String? = nil
    @Published var retry: (() -> Void)? = nil
    private var dismissTask: Task<Void, Never>? = nil

    func show(_ msg: String, retry: (() -> Void)? = nil) {
        dismissTask?.cancel()
        message = msg
        self.retry = retry
        if retry == nil {
            dismissTask = Task { [weak self] in
                try? await Task.sleep(for: .seconds(2.5))
                if !Task.isCancelled { self?.message = nil }
            }
        }
    }

    func dismiss() { dismissTask?.cancel(); message = nil; retry = nil }
}

struct ToastView: View {
    @ObservedObject var toaster: Toaster

    var body: some View {
        if let msg = toaster.message {
            HStack(spacing: 12) {
                Text(msg).font(Theme.monoSmall).foregroundStyle(Theme.fg).lineLimit(2)
                if let r = toaster.retry {
                    Button("retry") { toaster.dismiss(); r() }
                        .font(Theme.monoSmall.bold()).foregroundStyle(Theme.accent)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .background(Color(white: 0.16), in: RoundedRectangle(cornerRadius: 10))
            .padding(.horizontal, 16)
            .onTapGesture { if toaster.retry == nil { toaster.dismiss() } }
            .transition(.move(edge: .bottom).combined(with: .opacity))
            .animation(.easeOut(duration: 0.2), value: toaster.message)
        }
    }
}
