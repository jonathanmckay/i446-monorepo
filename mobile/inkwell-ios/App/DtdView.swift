import SwiftUI
import InkwellCore

struct DtdView: View {
    @ObservedObject var store: DtdStore
    var onSettings: () -> Void

    @State private var valuePrompt: DtdTask? = nil
    @State private var valueText = ""
    @State private var delayTarget: DtdTask? = nil
    @State private var delayOptions = DelayOptions.empty
    @State private var addText = ""
    @FocusState private var addFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(title: store.title, status: store.status, onLongPress: onSettings,
                       onRetry: { store.reload() })
            if store.agentCount > 0 { agentBanner }
            taskList
        }
        .background(Theme.bg)
        .safeAreaInset(edge: .bottom) {
            // New task at the bottom right (2026-10-20); + with an empty field
            // just focuses it.
            EntryBar(placeholder: "task (N) [pts] @code", text: $addText, focus: $addFocused,
                     onSubmit: { store.add($0) }, onPlus: { addFocused = true })
        }
        .task { if !store.loadedOnce { await store.load(refresh: false) } }
        .modifier(ValuePrompt(task: $valuePrompt, text: $valueText, store: store))
        .modifier(DelayDialog(task: $delayTarget, options: delayOptions, store: store))
    }

    /// Web's "◐ 😈 ×N": how many cards a Claude session is working right now.
    private var agentBanner: some View {
        HStack(spacing: 6) {
            Spinner().foregroundStyle(Theme.dim)
            Text("😈" + (store.agentCount > 1 ? " ×\(store.agentCount)" : "") + " agent working")
                .font(Theme.monoSmall).foregroundStyle(Theme.dim)
            Spacer()
        }
        .padding(.horizontal, 14).padding(.bottom, 4)
    }

    private var taskList: some View {
        List {
            ForEach(store.tasks) { t in
                TaskRow(task: t)
                    .listRowBackground(Theme.bg)
                    .listRowSeparatorTint(Color(white: 0.15))
                    .swipeActions(edge: .leading, allowsFullSwipe: true) { doneButton(t) }
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

    /// Leading edge = Android's right-swipe = done. A full swipe commits
    /// (user request 2026-10-20: "can't swipe all the way to complete");
    /// /api/done is idempotent by id since 2026-10-19, so a double fire or a
    /// retry can never double-credit. A variable habit still asks its value.
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
    /// "N days…" opens a number alert for this task (the CLI's typed-N row).
    @State private var customTask: DtdTask? = nil
    @State private var customText = ""

    func body(content: Content) -> some View {
        content.confirmationDialog("⏰ delay", isPresented: isPresented($task), titleVisibility: .visible,
                                   presenting: task) { t in
            ForEach(options.blocks, id: \.hour) { b in
                Button("\(b.glyph) " + String(format: "%02d:00", b.hour)) { store.delayHour(t, hour: b.hour) }
            }
            ForEach(options.minutes, id: \.self) { m in
                Button(m % 60 == 0 ? "+\(m / 60)h" : "+\(m)m") { store.delayMinutes(t, minutes: m) }
            }
            // Day rows, same set as the CLI schedule screen (↻ 0 / 📅 1天 / 2天 / 7天 / typed N).
            Button("↻ next occurrence / one day") { store.delayDays(t, days: 0) }
            Button("📅 +1 day") { store.delayDays(t, days: 1) }
            Button("📅 +2 days") { store.delayDays(t, days: 2) }
            Button("📅 +7 days") { store.delayDays(t, days: 7) }
            Button("✎ N days…") {
                // Let the dialog finish dismissing before the alert presents.
                Task { try? await Task.sleep(for: .milliseconds(400)); customText = ""; customTask = t }
            }
            Button("cancel", role: .cancel) {}
        } message: { t in
            Text(t.title)
        }
        .alert("delay N days", isPresented: isPresented($customTask), presenting: customTask) { t in
            TextField("days", text: $customText)
                #if os(iOS)
                .keyboardType(.numberPad)
                #endif
            Button("delay") {
                if let n = Int(customText.trimmingCharacters(in: .whitespaces)), (0...366).contains(n) {
                    store.delayDays(t, days: n)
                }
            }
            Button("cancel", role: .cancel) {}
        } message: { t in
            Text(t.title + " · 0 = next occurrence")
        }
    }
}

/// Color bar + title in the domain color, right-justified (time)[value] like
/// the terminal and the web.
struct TaskRow: View {
    let task: DtdTask
    var body: some View {
        // Agent-claimed rows go grey with 😈 (web: .row.agent); the task whose
        // timer is live gets ▶ and a brighter bar (2026-10-20 phone request).
        let color: Color = task.agent ? Theme.dim : Color(hex: task.color)
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 2).fill(Color(hex: task.color))
                .frame(width: task.running ? 6 : 4, height: 22)
            Text(task.prefix + task.title)
                .font(Theme.mono).foregroundStyle(color).lineLimit(2)
            Spacer(minLength: 8)
            if task.running { Spinner().foregroundStyle(Theme.accent) }
            Text(task.meta).font(Theme.monoSmall).foregroundStyle(Theme.dim)
        }
        .padding(.vertical, 4)
    }
}

/// The web's ◐ rotating glyph (1 rev/s), used beside a running row and in
/// the header when any 😈 agent is mid-turn.
struct Spinner: View {
    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 12)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            Text("◐").font(Theme.mono)
                .rotationEffect(.degrees((t.truncatingRemainder(dividingBy: 1)) * 360))
        }
    }
}

/// Optional-item presentation binding: non-nil → presented; dismiss → nil.
func isPresented<T>(_ item: Binding<T?>) -> Binding<Bool> {
    Binding(get: { item.wrappedValue != nil }, set: { if !$0 { item.wrappedValue = nil } })
}
