import SwiftUI
import VaultCore

/// Vault tab: an offline copy of the phone subset (z_meta/phone-subset.md)
/// and a capture box. Three ways in: recent (by mtime), browse (folders),
/// search (FTS5, local). Notes are read-only; the pencil captures a NEW note
/// into the outbox, which the next sync drops into z_ibx/phone/ on ix.
struct VaultView: View {
    @ObservedObject var store: VaultTabStore
    var onSettings: () -> Void

    /// new-notes IS the root screen (2026-10-20: "open to new-notes by
    /// default, not behind a button"); recent / browse / search are the
    /// other segments.
    enum Mode: String, CaseIterable { case inbox = "new-notes", recent, browse, search }
    @State private var mode: Mode = .inbox
    @State private var query = ""
    @State private var showCapture = false
    @State private var path = NavigationPath()
    static let landing = "z_ibx/new-notes.md"

    var body: some View {
        NavigationStack(path: $path) {
            VStack(spacing: 0) {
                // No capture button for now (2026-10-21): Inkwell on iOS is a
                // mobile entry screen for new-notes; the editor IS the input.
                // CaptureSheet stays reachable from the App Intent.
                HeaderView(title: store.title, status: store.status, onLongPress: onSettings)
                Picker("", selection: $mode) {
                    ForEach(Mode.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented).padding(.horizontal, 14).padding(.bottom, 6)
                content
            }
            .background(Theme.bg)
            .navigationDestination(for: String.self) { p in NoteView(store: store, path: p) }
            .navigationDestination(for: DirEntry.self) { d in FolderView(store: store, dir: d.path) }
        }
        .sheet(isPresented: $showCapture) { CaptureSheet(store: store) }
        .task { store.fetchLanding(VaultView.landing); store.syncIfStale() }
    }

    @ViewBuilder private var content: some View {
        switch mode {
        case .inbox:
            if store.read(VaultView.landing) != nil {
                NoteEditor(store: store, path: VaultView.landing)
            } else {
                VStack(spacing: 10) {
                    Text(store.syncing ? "syncing the vault…" : "new-notes is not on the phone yet")
                        .font(Theme.mono).foregroundStyle(Theme.dim)
                    if !store.syncing {
                        Button("sync now") { store.runSync(force: true) }
                            .font(Theme.mono).buttonStyle(.bordered).tint(Theme.accent)
                        if store.fileCount == 0 {
                            Text("needs the vault base + token · long-press the header")
                                .font(Theme.monoSmall).foregroundStyle(Theme.dim)
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity).background(Theme.bg)
            }
        case .recent:
            let items = store.recent()
            entryList(items, empty: store.fileCount == 0 ? "nothing synced yet · pull to sync" : "empty")
        case .browse:
            entryList(store.list(""), empty: "nothing synced yet · pull to sync")
        case .search:
            VStack(spacing: 0) {
                TextField("search notes", text: $query)
                    .font(Theme.mono).padding(10)
                    .background(Color(white: 0.1)).clipShape(RoundedRectangle(cornerRadius: 8))
                    .padding(.horizontal, 14).padding(.bottom, 6)
                    .autocorrectionDisabled()
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    #endif
                List(store.search(query), id: \.path) { hit in
                    NavigationLink(value: hit.path) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(VaultStore.basename(hit.path)).font(Theme.mono).foregroundStyle(Theme.fg)
                            Text(hit.snippet).font(Theme.monoSmall).foregroundStyle(Theme.dim).lineLimit(2)
                        }
                    }
                    .listRowBackground(Theme.bg)
                }
                .listStyle(.plain).scrollContentBackground(.hidden)
            }
        }
    }

    private func entryList(_ items: [DirEntry], empty: String) -> some View {
        List {
            ForEach(items, id: \.path) { e in
                if e.isFolder {
                    NavigationLink(value: e) { EntryRow(entry: e) }
                } else {
                    NavigationLink(value: e.path) { EntryRow(entry: e) }
                }
            }
            .listRowBackground(Theme.bg)
            if items.isEmpty {
                Text(empty).font(Theme.mono).foregroundStyle(Theme.dim)
                    .frame(maxWidth: .infinity).listRowBackground(Theme.bg)
            }
        }
        .listStyle(.plain).scrollContentBackground(.hidden)
        .refreshable { store.runSync(force: true); try? await Task.sleep(nanoseconds: 600_000_000) }
    }
}

struct EntryRow: View {
    let entry: DirEntry
    var body: some View {
        HStack {
            Image(systemName: entry.isFolder ? "folder" : "doc.text").foregroundStyle(entry.isFolder ? Theme.accent : Theme.dim)
            Text(entry.name).font(Theme.mono).foregroundStyle(Theme.fg).lineLimit(1)
            Spacer()
            if !entry.isFolder {
                Text(EntryRow.when(entry.mtime)).font(Theme.monoSmall).foregroundStyle(Theme.dim)
            }
        }
    }
    static func when(_ mtime: Int) -> String {
        let d = Date(timeIntervalSince1970: TimeInterval(mtime))
        let f = DateFormatter()
        f.dateFormat = Calendar.current.isDateInToday(d) ? "HH:mm" : "MM.dd"
        return f.string(from: d)
    }
}

struct FolderView: View {
    @ObservedObject var store: VaultTabStore
    let dir: String
    var body: some View {
        List {
            ForEach(store.list(dir), id: \.path) { e in
                if e.isFolder {
                    NavigationLink(value: e) { EntryRow(entry: e) }
                } else {
                    NavigationLink(value: e.path) { EntryRow(entry: e) }
                }
            }
            .listRowBackground(Theme.bg)
        }
        .listStyle(.plain).scrollContentBackground(.hidden).background(Theme.bg)
        .navigationTitle(dir.split(separator: "/").last.map(String.init) ?? dir)
    }
}

/// Read-only note. v1 renders the body as plain monospaced text (markdown
/// rendering comes after a week of real use); wikilinks are listed under
/// the body and resolve locally, with a "not on phone" stub for anything
/// outside the subset.
struct NoteView: View {
    @ObservedObject var store: VaultTabStore
    let path: String

    var body: some View {
        let text = store.read(path) ?? ""
        let (fm, body) = Frontmatter.split(text)
        let links = Wikilinks.parse(body)
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text(fm["title"] ?? VaultStore.basename(path)).font(.title3.monospaced()).foregroundStyle(Theme.fg)
                Text(path).font(Theme.monoSmall).foregroundStyle(Theme.dim)
                if let d = fm["date"] { Text(d).font(Theme.monoSmall).foregroundStyle(Theme.dim) }
                Divider().overlay(Color(white: 0.2))
                Text(body).font(Theme.mono).foregroundStyle(Theme.fg).textSelection(.enabled)
                if !links.isEmpty {
                    Divider().overlay(Color(white: 0.2))
                    Text("links").font(Theme.monoSmall).foregroundStyle(Theme.dim)
                    ForEach(Array(links.enumerated()), id: \.offset) { _, l in
                        if let target = store.resolve(l.target) {
                            NavigationLink(value: target) {
                                Label(Wikilinks.label(l), systemImage: "arrow.turn.down.right").font(Theme.mono).foregroundStyle(Theme.accent)
                            }
                        } else {
                            Label(Wikilinks.label(l) + " · not on phone", systemImage: "iphone.slash").font(Theme.mono).foregroundStyle(Theme.dim)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
        }
        .background(Theme.bg)
        .navigationTitle(VaultStore.basename(path))
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }
}

/// Capture box. Save = enqueue locally + kick a sync. Works with no network;
/// the header's "N pending" shows what has not reached ix yet.
struct CaptureSheet: View {
    @ObservedObject var store: VaultTabStore
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 8) {
                TextEditor(text: $text)
                    .font(Theme.mono)
                    .scrollContentBackground(.hidden)
                    .background(Theme.bg)
                    .focused($focused)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                Text("first line = title · `Name d359`, `… d358`, `o314` route it (see /notes)")
                    .font(Theme.monoSmall).foregroundStyle(Theme.dim)
                recentCaptures
            }
            .padding(14)
            .background(Theme.bg)
            .navigationTitle("capture")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("save") {
                        if store.capture(text) { Platform.haptic(.success); dismiss() } else { Platform.haptic(.failure) }
                    }
                    .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .onAppear { focused = true }
        }
        .preferredColorScheme(.dark)
    }

    @ViewBuilder private var recentCaptures: some View {
        let caps = store.captures().prefix(5)
        if !caps.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(Array(caps), id: \.id) { c in
                    HStack {
                        Image(systemName: c.sentPath == nil ? "clock" : "checkmark.circle")
                            .foregroundStyle(c.sentPath == nil ? Color.orange : Theme.done)
                        Text(c.body.split(separator: "\n").first.map(String.init) ?? "").font(Theme.monoSmall).foregroundStyle(Theme.dim).lineLimit(1)
                        Spacer()
                        if let e = c.lastError, c.sentPath == nil { Text(e).font(Theme.monoSmall).foregroundStyle(.orange).lineLimit(1) }
                    }
                }
            }
        }
    }
}


/// new-notes, editable like a regular note (2026-10-20). Edits save locally
/// as you type (debounced) and reach ix as one full-text put carrying the sha
/// of the server version this buffer derives from; if the file moved on
/// meanwhile the server three-way merges and sends the result back. Server
/// changes are adopted only while the editor is not dirty. Pull down to
/// refresh (2026-10-21): flushes a dirty buffer first, then reconciles.
struct NoteEditor: View {
    @ObservedObject var store: VaultTabStore
    let path: String
    @State private var text = ""
    @State private var saved = ""
    @State private var baseSha = ""
    @State private var loaded = false
    @State private var refreshing = false
    @State private var debounce: Task<Void, Never>? = nil

    var body: some View {
        VStack(spacing: 0) {
            RefreshableTextEditor(text: $text, onRefresh: { await refresh() })
                .padding(.horizontal, 8)
                .onChange(of: text) { _, new in
                    guard loaded, new != saved else { return }
                    debounce?.cancel()
                    debounce = Task {
                        try? await Task.sleep(for: .milliseconds(900))
                        guard !Task.isCancelled else { return }
                        save(new)
                    }
                }
            HStack {
                Text(path).font(Theme.monoSmall).foregroundStyle(Theme.dim)
                Spacer()
                Text(refreshing ? "refreshing…" : text == saved ? (store.pendingCount > 0 ? "saved · sending…" : "saved") : "editing…")
                    .font(Theme.monoSmall).foregroundStyle(refreshing ? Theme.accent : Theme.dim)
                if refreshing {
                    ProgressView().controlSize(.small).tint(Theme.accent).padding(.leading, 10)
                } else {
                    Button { Task { await refresh() } } label: {
                        Image(systemName: "arrow.clockwise")
                            .font(Theme.monoSmall).foregroundStyle(Theme.accent)
                    }
                    .disabled(store.syncing)
                    .padding(.leading, 10)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 6)
        }
        .onAppear { if !loaded { adopt(); loaded = true } }
        .onChange(of: store.landingVersion) { _, _ in
            // Server copy changed (fetch / sync / a put came back merged): take
            // it only if we have nothing unsent, so a local edit is never
            // clobbered. The base moves with it.
            if text == saved { adopt() }
        }
    }

    private func adopt() {
        baseSha = store.sha(path) ?? baseSha
        if let t = store.read(path), t != text { text = t; saved = t }
    }

    private func save(_ new: String) {
        saved = new
        store.saveNote(path, text: new, baseSha: baseSha)
    }

    /// Pull or tap: the spinner stays up for at least 0.7 s so a 200 ms round
    /// trip still reads as "it refreshed" (2026-10-21).
    private func refresh() async {
        guard !refreshing else { return }
        refreshing = true
        debounce?.cancel()
        if text != saved { save(text) }
        let started = Date()
        await store.refresh(path)
        let remaining = 0.7 - Date().timeIntervalSince(started)
        if remaining > 0 { try? await Task.sleep(for: .seconds(remaining)) }
        refreshing = false
    }
}
