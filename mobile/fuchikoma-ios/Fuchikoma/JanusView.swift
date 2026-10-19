import SwiftUI
import FuchikomaCore

struct JanusView: View {
    @ObservedObject var store: JanusStore
    var onSettings: () -> Void

    @State private var fillTarget: FillTarget? = nil
    @State private var editTarget: TimelineRow? = nil

    /// A gap to fill, or "start now" from the header's +.
    struct FillTarget: Identifiable {
        let id = UUID()
        let gap: TimelineRow?
    }

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(title: store.title, status: store.status, onLongPress: onSettings,
                       actionIcon: "plus", onAction: { fillTarget = FillTarget(gap: nil) })
            List {
                ForEach(Array(store.rows.enumerated()), id: \.offset) { _, r in
                    if r.isDivider {
                        Text(r.label).font(Theme.monoSmall).foregroundStyle(Theme.dim)
                            .listRowBackground(Theme.bg).listRowSeparator(.hidden)
                    } else {
                        TimelineRowView(row: r)
                            .listRowBackground(Theme.bg)
                            .listRowSeparatorTint(Color(white: 0.15))
                            .swipeActions(edge: .leading, allowsFullSwipe: r.isEntry && !r.running) {
                                // Full-swipe commit only for a plain entry → /api/log, which
                                // the server's per-day ledger makes idempotent. Everything
                                // else (stop+log, convert, fill) reveals then taps.
                                if r.canSwipeRight {
                                    Button {
                                        if r.isGap { fillTarget = FillTarget(gap: r) } else { store.onRight(r) }
                                    } label: { Label(r.rightLabel, systemImage: r.isGap ? "plus" : "checkmark") }
                                    .tint(r.isGap ? .blue : Theme.done)
                                }
                            }
                            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                if r.canSwipeLeft {
                                    Button { editTarget = r } label: { Label("edit / split", systemImage: "pencil") }
                                        .tint(Theme.edit)
                                }
                            }
                            .disabled(store.busy)
                    }
                }
                if store.rows.isEmpty && store.loadedOnce {
                    Text("nothing tracked yet").font(Theme.mono).foregroundStyle(Theme.dim)
                        .frame(maxWidth: .infinity).listRowBackground(Theme.bg)
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(Theme.bg)
            .refreshable { await store.load() }
        }
        .background(Theme.bg)
        .task { if !store.loadedOnce { await store.load() } }
        .sheet(item: $fillTarget) { t in
            FillSheet(gap: t.gap) { desc, start, end in store.fill(desc: desc, start: start, end: end) }
        }
        .sheet(item: $editTarget) { r in
            EditSheet(row: r, loadCodes: { await store.projectCodes() },
                      onSave: { d, s, e, p in store.edit(r, desc: d, start: s, end: e, project: p) },
                      onSplit: { mode in store.split(r, mode: mode) })
        }
    }
}

/// `.sheet(item:)` identity = the Toggl id (edit sheets open on entries only;
/// list rows themselves are keyed by position since gaps have no id).
extension TimelineRow: Identifiable {}

extension View {
    @ViewBuilder func sheetMedium() -> some View {
        #if os(iOS)
        self.presentationDetents([.medium])
        #else
        self
        #endif
    }
    @ViewBuilder func sheetLarge() -> some View {
        #if os(iOS)
        self.presentationDetents([.large])
        #else
        self
        #endif
    }
}

struct TimelineRowView: View {
    let row: TimelineRow
    var body: some View {
        let color: Color = row.isGap ? Theme.dim : Color(hex: row.color)
        HStack(spacing: 10) {
            Text(row.displayTitle).font(Theme.mono).foregroundStyle(color).lineLimit(2)
                .opacity(row.logged && !row.running ? 0.55 : 1)
            Spacer(minLength: 8)
            Text(row.displayMeta).font(Theme.monoSmall).foregroundStyle(Theme.dim)
        }
        .padding(.vertical, 4)
    }
}

/// Fill a gap (start/end prefilled) or start something "now" (ongoing).
struct FillSheet: View {
    let gap: TimelineRow?
    var onSave: (String, String, String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var desc = ""
    @State private var start = ""
    @State private var end = ""
    @State private var ongoing = false

    var body: some View {
        NavigationStack {
            Form {
                TextField("description  (@code picks the project)", text: $desc)
                TextField("start HH:MM", text: $start)
                Toggle("ongoing (no end)", isOn: $ongoing)
                if !ongoing { TextField("end HH:MM", text: $end) }
            }
            .font(Theme.mono)
            .navigationTitle(gap.map { "fill \($0.start)–\($0.end)" } ?? "start now")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("save") {
                        let d = desc.trimmingCharacters(in: .whitespaces)
                        guard !d.isEmpty else { Toaster.shared.show("description required"); return }
                        onSave(d, start.trimmingCharacters(in: .whitespaces),
                               ongoing ? "" : end.trimmingCharacters(in: .whitespaces))
                        dismiss()
                    }
                }
            }
        }
        .onAppear {
            if let g = gap { start = g.start; end = g.end }
            else {
                let f = DateFormatter(); f.dateFormat = "HH:mm"
                start = f.string(from: Date()); ongoing = true
            }
        }
        .sheetMedium()
    }
}

/// Edit desc / times / project, or split the entry top / bottom.
struct EditSheet: View {
    let row: TimelineRow
    var loadCodes: () async -> [String]
    var onSave: (String, String, String, String) -> Void
    var onSplit: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var desc = ""
    @State private var start = ""
    @State private var end = ""
    @State private var project = ""
    @State private var codes: [String] = []

    var body: some View {
        NavigationStack {
            Form {
                TextField("description", text: $desc)
                TextField("start HH:MM", text: $start)
                TextField(row.running ? "end (blank = still running)" : "end HH:MM", text: $end)
                Picker("project", selection: $project) {
                    ForEach(codes, id: \.self) { Text($0.isEmpty ? "–" : $0).tag($0) }
                }
                Section {
                    Button("split ▲ (keep top)") { onSplit("top"); dismiss() }
                    Button("split ▼ (keep bottom)") { onSplit("bottom"); dismiss() }
                }
            }
            .font(Theme.mono)
            .navigationTitle("edit \(row.start)–\(row.end)")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("save") {
                        onSave(desc.trimmingCharacters(in: .whitespaces), start.trimmingCharacters(in: .whitespaces),
                               end.trimmingCharacters(in: .whitespaces), project)
                        dismiss()
                    }
                }
            }
        }
        .onAppear {
            desc = row.desc; start = row.start; end = row.running ? "" : row.end; project = row.project
            codes = [row.project]
        }
        .task {
            var all = await loadCodes()
            if !all.contains(row.project) { all.insert(row.project, at: 0) }
            if !all.isEmpty { codes = all }
        }
        .sheetLarge()
    }
}
