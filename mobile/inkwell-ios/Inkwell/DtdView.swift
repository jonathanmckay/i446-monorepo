import SwiftUI
import InkwellCore

struct DtdView: View {
    @ObservedObject var store: DtdStore
    var onSettings: () -> Void

    @State private var valuePrompt: DtdTask? = nil
    @State private var valueText = ""
    @State private var delayTarget: DtdTask? = nil
    @State private var delayOptions = DelayOptions.empty
    @State private var showAdd = false
    @State private var addText = ""

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(title: store.title, status: store.status, onLongPress: onSettings,
                       actionIcon: "plus", onAction: { addText = ""; showAdd = true })
            taskList
        }
        .background(Theme.bg)
        .task { if !store.loadedOnce { await store.load(refresh: false) } }
        .modifier(ValuePrompt(task: $valuePrompt, text: $valueText, store: store))
        .modifier(DelayDialog(task: $delayTarget, options: delayOptions, store: store))
        .alert("add task", isPresented: $showAdd) {
            TextField("task (N) [pts] @code", text: $addText)
            Button("add") { store.add(addText) }
            Button("cancel", role: .cancel) {}
        }
    }

    private var taskList: some View {
        List {
            ForEach(store.tasks) { t in
                TaskRow(task: t)
                    .listRowBackground(Theme.bg)
                    .listRowSeparatorTint(Color(white: 0.15))
                    .swipeActions(edge: .leading, allowsFullSwipe: false) { doneButton(t) }
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) { trailingButtons(t) }
                    .disabled(store.inFlight.contains(t.id))
            }
            if store.tasks.isEmpty && store.loadedOnce {
                Text("nothing due · 🎉").font(Theme.mono).foregroundStyle(Theme.dim)
                    .frame(maxWidth: .infinity).listRowBackground(Theme.bg)
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
        .refreshable { await store.load(refresh: true) }
    }

    /// Leading edge = Android's right-swipe = done. No full-swipe commit:
    /// SwiftUI's threshold isn't tunable and /api/done runs the real /did —
    /// reveal, then tap (one extra tap, zero accidental completions).
    private func doneButton(_ t: DtdTask) -> some View {
        Button {
            if t.variablePrompt { valueText = ""; valuePrompt = t } else { store.complete(t, value: nil) }
        } label: {
            Label(t.points.map { "done +\($0)分" } ?? "done", systemImage: "checkmark")
        }
        .tint(Theme.done)
    }

    @ViewBuilder
    private func trailingButtons(_ t: DtdTask) -> some View {
        Button { store.start(t) } label: { Label("start", systemImage: "play.fill") }.tint(.blue)
        Button { store.delayDay(t) } label: { Label("+1d", systemImage: "calendar") }.tint(.orange)
        Button { openDelay(t) } label: { Label("delay", systemImage: "alarm") }.tint(.gray)
    }

    private func openDelay(_ t: DtdTask) {
        Task {
            delayOptions = await store.delayOptions() ?? .empty
            delayTarget = t
        }
    }
}

/// Variable habit: ask for its value before completing (web's submitVal).
private struct ValuePrompt: ViewModifier {
    @Binding var task: DtdTask?
    @Binding var text: String
    let store: DtdStore

    func body(content: Content) -> some View {
        content.alert(task?.title ?? "", isPresented: isPresented($task), presenting: task) { t in
            TextField("value", text: $text)
                #if os(iOS)
                .keyboardType(.numberPad)
                #endif
            Button("done") { store.complete(t, value: text) }
            Button("skip") { store.complete(t, value: "") }
            Button("cancel", role: .cancel) {}
        } message: { _ in
            Text("value?")
        }
    }
}

/// ⏰ picker: remaining 地支 blocks today plus the fixed minute trio, fetched
/// fresh each time it opens so the block list is never stale.
private struct DelayDialog: ViewModifier {
    @Binding var task: DtdTask?
    let options: DelayOptions
    let store: DtdStore

    func body(content: Content) -> some View {
        content.confirmationDialog("⏰ delay", isPresented: isPresented($task), titleVisibility: .visible,
                                   presenting: task) { t in
            ForEach(options.blocks, id: \.hour) { b in
                Button("\(b.glyph) " + String(format: "%02d:00", b.hour)) { store.delayHour(t, hour: b.hour) }
            }
            ForEach(options.minutes, id: \.self) { m in
                Button(m % 60 == 0 ? "+\(m / 60)h" : "+\(m)m") { store.delayMinutes(t, minutes: m) }
            }
            if options.isEmpty { Button("nothing later today") {} }
            Button("cancel", role: .cancel) {}
        } message: { t in
            Text(t.title)
        }
    }
}

/// Color bar + title in the domain color, right-justified (time)[value] like
/// the terminal and the web.
struct TaskRow: View {
    let task: DtdTask
    var body: some View {
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 2).fill(Color(hex: task.color)).frame(width: 4, height: 22)
            Text((task.recurring ? "↻ " : "") + task.title)
                .font(Theme.mono).foregroundStyle(Color(hex: task.color)).lineLimit(2)
            Spacer(minLength: 8)
            Text(task.meta).font(Theme.monoSmall).foregroundStyle(Theme.dim)
        }
        .padding(.vertical, 4)
    }
}

/// Optional-item presentation binding: non-nil → presented; dismiss → nil.
func isPresented<T>(_ item: Binding<T?>) -> Binding<Bool> {
    Binding(get: { item.wrappedValue != nil }, set: { if !$0 { item.wrappedValue = nil } })
}
