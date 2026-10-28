import SwiftUI
import InkwellCore

struct JanusView: View {
    @ObservedObject var store: JanusStore
    var onSettings: () -> Void

    @State private var fillTarget: FillTarget? = nil
    @State private var editTarget: TimelineRow? = nil
    @State private var deleteTarget: TimelineRow? = nil
    @State private var pointsText = ""

    /// A gap to fill, or "start now" from the header's +.
    struct FillTarget: Identifiable {
        let id = UUID()
        let gap: TimelineRow?
    }

    @State private var cmd = ""
    @FocusState private var cmdFocused: Bool

    private func scrollToDefault(_ proxy: ScrollViewProxy, animated: Bool) {
        guard let i = store.defaultScrollIndex else { return }
        // Next runloop: the List must have laid out the just-published rows.
        DispatchQueue.main.async {
            if animated { withAnimation { proxy.scrollTo(i, anchor: .center) } }
            else { proxy.scrollTo(i, anchor: .center) }
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(title: store.title, status: store.status, onLongPress: onSettings,
                       onRetry: { store.reload() })
            // Back/forward day navigation (2026-10-22): ‹ steps to earlier days
            // (the CLI's ctrl+/), › returns toward today (ctrl+=), disabled on
            // today. A past day shows its date + a "today" jump; its rows swipe
            // like today's (2026-10-26), only the bottom bar hides.
            // Each control is a ≥44×44 pt hit area (2026-10-26: "tap target to
            // previous days is too small"); the glyphs stay the same size, and
            // the bar's own padding shrinks to absorb the taller targets.
            HStack(spacing: 0) {
                Button { store.prevDay() } label: { Image(systemName: "chevron.left").dayNavTarget() }
                    .buttonStyle(.plain)
                if !store.viewingToday {
                    Text(store.dayLabel).foregroundStyle(Theme.accent)
                    Button { store.goToday() } label: { Text("today").dayNavTarget() }.buttonStyle(.plain)
                }
                Spacer()
                Button { store.nextDay() } label: { Image(systemName: "chevron.right").dayNavTarget() }
                    .buttonStyle(.plain)
                    .disabled(store.viewingToday)
                    .opacity(store.viewingToday ? 0.3 : 1)
            }
            .font(Theme.monoSmall).foregroundStyle(Theme.dim)
            .padding(.horizontal, 2)
            .background(Theme.bg)
            ScrollViewReader { proxy in
            List {
                ForEach(Array(store.rows.enumerated()), id: \.offset) { _, r in
                    if r.isDivider {
                        Text(r.label).font(Theme.monoSmall).foregroundStyle(Theme.dim)
                            .listRowBackground(Theme.bg).listRowSeparator(.hidden)
                    } else {
                        TimelineRowView(row: r)
                            .listRowBackground(Theme.bg)
                            .listRowSeparatorTint(Color(white: 0.15))
                            // Swipe right all the way = did (2026-10-22 janus note #2 + round 3
                            // #2): log / stop+log / convert / fill fire on a full swipe, the
                            // label shows the 分 it will credit, and a success haptic lands
                            // once the server registers it. /api/log is idempotent by the
                            // server ledger; done-current/convert are guarded in-flight.
                            // Past days swipe too (2026-10-26): writes carry the viewed
                            // date; an event there only logs (is_past), never starts.
                            .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                if r.canSwipeRight && (store.viewingToday || !r.isEvent || r.isPast) {
                                    Button {
                                        if r.isGap { fillTarget = FillTarget(gap: r) } else { store.onRight(r) }
                                    } label: { Label(r.rightLabel, systemImage: r.isGap ? "plus" : "checkmark") }
                                    .tint(r.isGap ? .blue : Theme.done)
                                }
                            }
                            // Swipe left all the way = resume (a fresh timer, same desc +
                            // project); half way reveals the options (2026-10-25: "add
                            // delete on swipe left, combine split and edit buttons"):
                            // edit (the sheet carries split ▲/▼) · delete (confirmed;
                            // not for a running or already-logged entry, which the
                            // server refuses anyway). Tint, not .destructive role: that
                            // role animates the row away before the confirm.
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                if r.isEvent {
                                    // Calendar event (2026-10-25): delete only.
                                    Button { deleteTarget = r } label: { Label("delete", systemImage: "trash") }
                                        .tint(.red)
                                } else if r.canSwipeLeft && !r.isLocal && !r.isPending {
                                    if !r.running {
                                        Button { store.resume(r) } label: { Label("resume", systemImage: "play.fill") }
                                            .tint(.blue)
                                    }
                                    Button { editTarget = r } label: { Label("edit · split", systemImage: "pencil") }
                                        .tint(Theme.edit)
                                    if !r.running && !r.logged {
                                        Button { deleteTarget = r } label: { Label("delete", systemImage: "trash") }
                                            .tint(.red)
                                    }
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
            .refreshable { store.requestDefaultScroll(); await store.load() }
            // Default position = the last completed entry (2026-10-26), on
            // open (cached paint included), day switch, refresh, and after a
            // new time is set; the store bumps scrollTick for each.
            .onAppear { scrollToDefault(proxy, animated: false) }
            .onChange(of: store.scrollTick) { _, _ in scrollToDefault(proxy, animated: true) }
            }
        }
        .background(Theme.bg)
        .safeAreaInset(edge: .bottom) {
            // Live timer card sits just above the command bar (2026-10-21: moved
            // down from the header). Shown only while something is running. The
            // whole bottom bar (timer + command bar) hides on a past-day view —
            // both act on today, so there is nothing to do here.
            if store.viewingToday {
                VStack(spacing: 0) {
                    if let r = store.running {
                        CurrentTimerCard(row: r, busy: store.busy || r.isPending,
                                         onDone: { t in store.onRight(t) }, onEdit: { t in editTarget = t })
                    }
                    // Command bar + "start now" at the bottom right (2026-10-20).
                    EntryBar(placeholder: "coffee 1400-1415 @epcn · same as the CLI", text: $cmd,
                             focus: $cmdFocused, disabled: store.busy,
                             onSubmit: { store.run($0) },
                             onPlus: { fillTarget = FillTarget(gap: nil) })
                }
            }
        }
        .task {
            if !store.loadedOnce { await store.load() }
            // "open with the text box" (2026-10-19): the command bar takes focus on launch.
            try? await Task.sleep(for: .milliseconds(350))
            cmdFocused = true
        }
        .sheet(item: $fillTarget) { t in
            FillSheet(gap: t.gap) { desc, start, end, project in
                store.fill(desc: desc, start: start, end: end, project: project) }
        }
        .confirmationDialog(deleteTarget?.isEvent == true ? "delete this calendar event?" : "delete this Toggl entry?",
                            isPresented: isPresented($deleteTarget),
                            titleVisibility: .visible, presenting: deleteTarget) { r in
            Button("delete", role: .destructive) { store.delete(r) }
            Button("cancel", role: .cancel) {}
        } message: { r in
            Text("\(r.start)–\(r.end) " + (r.isEvent ? r.title + (r.eventId.isEmpty || r.calendarId.hasSuffix("@import.calendar.google.com")
                                                          ? " · Outlook: hidden from janus only" : " · removed from Google Calendar")
                                               : r.desc))
        }
        .sheet(item: $editTarget) { r in
            EditSheet(row: r, loadCodes: { await store.projectCodes() },
                      onSave: { d, s, e, p in store.edit(r, desc: d, start: s, end: e, project: p) },
                      onSplit: { mode in store.split(r, mode: mode) })
        }
        // Same question the CLI asks (lib/janus_done.py): a total or a rate.
        .alert("points?", isPresented: isPresented($store.pointsAsk), presenting: store.pointsAsk) { a in
            TextField("40 or .5/m", text: $pointsText)
            Button("log") { store.answerPoints(a, pointsText); pointsText = "" }
            Button("cancel", role: .cancel) { pointsText = "" }
        } message: { a in
            Text("\(a.desc) · \(a.minutes)m")
        }
    }
}

/// The live timer, pinned above the list: description in its project color,
/// project · since · elapsed (ticks every second), done / edit buttons.
struct CurrentTimerCard: View {
    let row: TimelineRow?
    let busy: Bool
    var onDone: (TimelineRow) -> Void
    var onEdit: (TimelineRow) -> Void

    var body: some View {
        Group {
            if let r = row {
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    HStack(spacing: 10) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("▶ " + r.desc + (r.tags.isEmpty ? "" : "  #" + r.tags.joined(separator: " #")))
                                .font(Theme.mono).foregroundStyle(Color(hex: r.color)).lineLimit(2)
                            Text([r.project.isEmpty ? nil : "@" + r.project, "since " + r.start,
                                  CurrentTimerCard.elapsed(since: r.start, now: ctx.date),
                                  r.points.map { "[\($0)]" }].compactMap { $0 }.joined(separator: " · "))
                                .font(Theme.monoSmall).foregroundStyle(Theme.dim)
                        }
                        Spacer(minLength: 6)
                        Button { onEdit(r) } label: { Image(systemName: "pencil").font(.title3) }
                            .buttonStyle(.plain).foregroundStyle(Theme.edit)
                        Button { onDone(r) } label: { Image(systemName: "checkmark.circle.fill").font(.title) }
                            .buttonStyle(.plain).foregroundStyle(Theme.done)
                    }
                    .disabled(busy)
                }
            } else {
                Text("no timer running · type above to start one")
                    .font(Theme.monoSmall).foregroundStyle(Theme.dim)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(Color(white: 0.07)).clipShape(RoundedRectangle(cornerRadius: 8))
        .padding(.horizontal, 14).padding(.bottom, 6)
    }

    /// "HH:MM" start today → "1h07m" / "42m". Past midnight the start was
    /// yesterday; janus splits at the day barrier, so treat it as today.
    static func elapsed(since hhmm: String, now: Date) -> String {
        let parts = hhmm.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 2 else { return "" }
        var c = Calendar.current.dateComponents([.year, .month, .day], from: now)
        c.hour = parts[0]; c.minute = parts[1]; c.second = 0
        guard let start = Calendar.current.date(from: c) else { return "" }
        var secs = Int(now.timeIntervalSince(start))
        if secs < 0 { secs += 86400 }
        let h = secs / 3600, m = (secs % 3600) / 60, s = secs % 60
        return h > 0 ? String(format: "%dh%02dm", h, m) : String(format: "%dm%02ds", m, s)
    }
}

/// `.sheet(item:)` identity = the Toggl id (edit sheets open on entries only;
/// list rows themselves are keyed by position since gaps have no id).
extension TimelineRow: Identifiable {}

extension View {
    /// A day-nav control's hit area: at least 44×44 pt, all of it tappable.
    func dayNavTarget() -> some View {
        frame(minWidth: 44, minHeight: 44).contentShape(Rectangle())
    }
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
        // start · title … duration · 分 (2026-10-22 janus notes #3/#4: start
        // time only on the left, time and points right-justified).
        HStack(spacing: 10) {
            Text(row.displayMeta).font(Theme.mono).foregroundStyle(Theme.dim)
                .fixedSize(horizontal: true, vertical: false)
            Text(row.displayTitle).font(Theme.mono).foregroundStyle(color).lineLimit(2)
                .opacity(row.logged && !row.running ? 0.55 : 1)
            Spacer(minLength: 8)
            Text(row.rightMeta).font(Theme.mono).foregroundStyle(Theme.dim)
                .fixedSize(horizontal: true, vertical: false)
        }
        .padding(.vertical, 4)
    }
}

/// Fill a gap (start/end prefilled) or start something "now" (ongoing).
struct FillSheet: View {
    let gap: TimelineRow?
    var onSave: (String, String, String, String) -> Void   // desc, start, end, project
    @Environment(\.dismiss) private var dismiss
    @State private var desc = ""
    @State private var start = ""
    @State private var end = ""
    @State private var project = ""
    @State private var ongoing = false
    @FocusState private var descFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                TextField("description", text: $desc).focused($descFocused)
                TextField("project code (or @code in the description)", text: $project)
                    .autocorrectionDisabled()
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    #endif
                TextField("start HH:MM", text: $start)
                Toggle("ongoing (no end)", isOn: $ongoing)
                if !ongoing { TextField("end HH:MM", text: $end) }
            }
            .font(Theme.mono)
            .navigationTitle(gap.map { "fill \($0.start)–\($0.end)" } ?? "start now")
            // Shared KeyRow (2026-10-25) as a sheet keyboard toolbar: inside
            // this NavigationStack it is the keyboard accessory, so the Form
            // scrolls the focused field above it rather than under it.
            .keyRowToolbar()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("save") {
                        let d = desc.trimmingCharacters(in: .whitespaces)
                        guard !d.isEmpty else { Toaster.shared.show("description required"); return }
                        onSave(d, start.trimmingCharacters(in: .whitespaces),
                               ongoing ? "" : end.trimmingCharacters(in: .whitespaces),
                               project.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "@")))
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
            descFocused = true
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
                TextField("project code (free text)", text: $project)
                    .autocorrectionDisabled()
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    #endif
                if codes.count > 1 {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(codes.filter { !$0.isEmpty }, id: \.self) { c in
                                Button(c) { project = c }
                                    .font(Theme.monoSmall).buttonStyle(.bordered)
                                    .tint(c == project ? Theme.accent : Theme.dim)
                            }
                        }
                    }
                }
                Section {
                    Button("split ▲ (keep top)") { onSplit("top"); dismiss() }
                    Button("split ▼ (keep bottom)") { onSplit("bottom"); dismiss() }
                }
            }
            .font(Theme.mono)
            .navigationTitle("edit \(row.start)–\(row.end)")
            .keyRowToolbar()   // same KeyRow as the fill sheet (2026-10-25)
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
