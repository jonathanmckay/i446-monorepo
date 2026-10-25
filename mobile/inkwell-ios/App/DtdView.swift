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
    @State private var editing: (task: DtdTask, fields: DtdEditFields)? = nil
    @State private var splitTarget: DtdTask? = nil
    @State private var formOpen: (task: DtdTask, form: DtdForm)? = nil
    @FocusState private var addFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(title: store.title, status: store.status, onLongPress: onSettings,
                       onRetry: { store.reload() })
            filterBar
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
        .sheet(isPresented: Binding(get: { editing != nil }, set: { if !$0 { editing = nil } })) {
            if let e = editing {
                DtdEditSheet(task: e.task, fields: e.fields) { f in store.applyEdit(e.task, f) }
            }
        }
        .sheet(item: $splitTarget) { t in
            DtdSplitSheet(task: t) { pts, mins, name, keep in store.split(t, pts: pts, minutes: mins, name: name, keep: keep) }
        }
        .sheet(isPresented: Binding(get: { formOpen != nil }, set: { if !$0 { formOpen = nil } })) {
            if let o = formOpen {
                DtdFormSheet(form: o.form) { values in store.submitForm(o.task, o.form, values: values) }
            }
        }
    }

    /// Fuzzy filter box (wave 2 #1, 2026-10-22), the native mirror of the dtd
    /// web filter. Typing narrows the list as a subsequence match; submitting a
    /// query that matches nothing "refills" — moves the text into the add bar
    /// and focuses it, so a failed search becomes a new task (one return to
    /// filter, another to add), like the terminal dtd's one dual-purpose box.
    private var filterBar: some View {
        HStack(spacing: 8) {
            Image(systemName: "line.3.horizontal.decrease.circle").foregroundStyle(Theme.dim)
            TextField("filter — words or a domain (xk87)", text: $store.filter)
                .font(Theme.mono).autocorrectionDisabled()
                #if os(iOS)
                .textInputAutocapitalization(.never)
                #endif
                .submitLabel(.search)
                .onSubmit {
                    if !store.filter.isEmpty && store.visibleTasks.isEmpty {
                        addText = store.filter; store.filter = ""; addFocused = true
                    }
                }
            if !store.filter.isEmpty {
                Button { store.filter = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(Theme.dim)
                }.buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 6)
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
        // ScrollViewReader so a task becoming active (▶ start / 😈 claim) can
        // scroll the list back to the top where it was just reordered.
        ScrollViewReader { proxy in
            List {
                ForEach(store.visibleTasks) { t in
                    TaskRow(task: t)
                        .id(t.id)
                        .listRowBackground(Theme.bg)
                        .listRowSeparatorTint(Color(white: 0.15))
                        .swipeActions(edge: .leading, allowsFullSwipe: true) { doneButton(t) }
                        .swipeActions(edge: .trailing, allowsFullSwipe: true) { trailingButtons(t) }
                        .disabled(store.inFlight.contains(t.id))
                }
                if store.visibleTasks.isEmpty && store.loadedOnce {
                    Text(store.filter.isEmpty ? "nothing due · 🎉" : "no match · ↵ to add “\(store.filter)”")
                        .font(Theme.mono).foregroundStyle(Theme.dim)
                        .frame(maxWidth: .infinity).listRowBackground(Theme.bg)
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(Theme.bg)
            #if os(iOS)
            // Drag the list to dismiss the entry keyboard (complements the
            // keyboard toolbar's "Done" in EntryBar).
            .scrollDismissesKeyboard(.interactively)
            #endif
            .refreshable { await store.load(refresh: true) }
            .onChange(of: store.scrollTick) {
                guard let top = store.tasks.first?.id else { return }
                withAnimation { proxy.scrollTo(top, anchor: .top) }
            }
        }
    }

    /// Leading edge = Android's right-swipe = done. A full swipe commits
    /// (user request 2026-10-20: "can't swipe all the way to complete");
    /// /api/done is idempotent by id since 2026-10-19, so a double fire or a
    /// retry can never double-credit. A variable habit still asks its value.
    private func doneButton(_ t: DtdTask) -> some View {
        Button {
            if !t.form.isEmpty { openForm(t) }
            else if t.variablePrompt { valueText = ""; valuePrompt = t } else { store.complete(t, value: nil) }
        } label: {
            if !t.form.isEmpty { Label("form", systemImage: "list.bullet.clipboard") }
            else { Label(t.points.map { "done +\($0)分" } ?? "done", systemImage: "checkmark") }
        }
        .tint(Theme.done)
    }

    /// Trailing edge = left swipe. `start` is the primary action (declared
    /// first, so it sits at the edge) and a full left swipe fires it directly;
    /// +1d / delay remain as secondary buttons a partial swipe reveals.
    @ViewBuilder
    private func trailingButtons(_ t: DtdTask) -> some View {
        Button { store.start(t) } label: { Label("start", systemImage: "play.fill") }.tint(.blue)
        Button { store.delayDay(t) } label: { Label("+1d", systemImage: "calendar") }.tint(.orange)
        Button { openDelay(t) } label: { Label("delay", systemImage: "alarm") }.tint(.gray)
        // Round 3 #5: edit (ctrl-g) and split / done-so-far (ctrl-p), as in the CLI.
        Button { openEdit(t) } label: { Label("edit", systemImage: "pencil") }.tint(Theme.edit)
        Button { splitTarget = t } label: { Label("split", systemImage: "scissors") }.tint(.teal)
    }

    private func openEdit(_ t: DtdTask) {
        Task {
            if let f = await store.editFields(t) { editing = (t, f) }
            else { Toaster.shared.show("couldn't load \(t.title) for editing") }
        }
    }

    /// Survey card (0s, 1s, xk887, …): fetch its form and open it, like
    /// Enter on the card in terminal dtd. No form on the server → complete.
    private func openForm(_ t: DtdTask) {
        Task {
            switch await store.form(t) {
            case .form(let f): formOpen = (t, f)
            case .none:
                if t.variablePrompt { valueText = ""; valuePrompt = t } else { store.complete(t, value: nil) }
            case .failed: break
            }
        }
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
        } message: { t in
            // The server's own question ("CPAP quality (1-3)", "xk22 minutes (Ren)"), 2026-10-25.
            Text(t.variableLabel.isEmpty ? "value?" : t.variableLabel)
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
            if task.running { Abacus().foregroundStyle(Theme.accent) }
            // Time/value meta at the task-text size (wave 2 #2, 2026-10-22):
            // the (time)[pts] column was monoSmall against a mono title and
            // read as an afterthought; match it to the title.
            Text(task.meta).font(Theme.mono).foregroundStyle(Theme.dim)
        }
        .padding(.vertical, 4)
    }
}

/// The 🧮 abacus glyph marking the one task whose Toggl timer is live. The
/// running row is a state (分 are being counted), not an indeterminate wait,
/// so it gets a steady abacus rather than the ◐ spinner. SF Symbol `abacus`
/// is iOS 18+; the app targets iOS 17, so a Text glyph is the equivalent.
struct Abacus: View {
    var body: some View {
        Text("🧮").font(Theme.mono)
    }
}

/// The web's ◐ rotating glyph (1 rev/s), used in the header when any 😈 agent
/// is mid-turn.
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
