import SwiftUI
import InkwellCore

/// Swipe-left "edit" (round 3 #5, 2026-10-22): terminal dtd's ctrl-g fields —
/// name, domain, [N] points, notes. edit-fast on the server applies only the
/// fields that changed, so untouched ones are safe to send back as-is.
struct DtdEditSheet: View {
    let task: DtdTask
    @State var fields: DtdEditFields
    var onSave: (DtdEditFields) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                TextField("name", text: $fields.name, axis: .vertical)
                TextField("points [N]", text: $fields.points)
                    #if os(iOS)
                    .keyboardType(.numberPad)
                    #endif
                TextField("domain", text: $fields.domain)
                    .autocorrectionDisabled()
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    #endif
                if !fields.domains.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(fields.domains, id: \.self) { d in
                                Button(d) { fields.domain = d }
                                    .font(Theme.monoSmall).buttonStyle(.bordered)
                                    .tint(d == fields.domain ? Theme.accent : Theme.dim)
                            }
                        }
                    }
                }
                Section("notes") {
                    TextField("notes", text: $fields.desc, axis: .vertical).lineLimit(3...10)
                }
            }
            .font(Theme.mono)
            .navigationTitle("edit")
            // Coding keys on the details page too (2026-10-25: "details page
            // needs extra keys"): the shared KeyRow, inserting at the caret
            // of whichever field (name / notes / ...) is being typed in.
            .keyRowToolbar()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("save") { onSave(fields); dismiss() }
                }
            }
        }
        .sheetLarge()
    }

    init(task: DtdTask, fields: DtdEditFields, onSave: @escaping (DtdEditFields) -> Void) {
        self.task = task; _fields = State(initialValue: fields); self.onSave = onSave
    }
}

/// Swipe-left "split" = terminal dtd's ctrl-p "done so far": credit part of
/// the task now (points and/or minutes, optionally under its own name) and
/// keep the remainder on the list.
struct DtdSplitSheet: View {
    let task: DtdTask
    var onSplit: (Int?, Int?, String, Bool) -> Void   // pts, minutes, done-as name, keep remainder
    @Environment(\.dismiss) private var dismiss
    @State private var pts = ""
    @State private var mins = ""
    @State private var name = ""
    @State private var keep = true
    @FocusState private var ptsFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("pts done" + (task.points.map { " (of \($0))" } ?? ""), text: $pts)
                        .focused($ptsFocused)
                    TextField("min done" + (task.est.map { " (of \($0))" } ?? ""), text: $mins)
                }
                #if os(iOS)
                .keyboardType(.numberPad)
                #endif
                TextField("done as (blank = same name)", text: $name)
                Toggle("keep the rest on the list", isOn: $keep)
            }
            .font(Theme.mono)
            .navigationTitle("✂ " + task.title)
            .keyRowToolbar()   // same KeyRow as the edit sheet (2026-10-25)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("split") {
                        let p = Int(pts.trimmingCharacters(in: .whitespaces))
                        let m = Int(mins.trimmingCharacters(in: .whitespaces))
                        // The server (split-fast, like the terminal form) needs pts done.
                        guard p != nil else { Toaster.shared.show("pts done required"); return }
                        onSplit(p, m, name.trimmingCharacters(in: .whitespaces), keep)
                        dismiss()
                    }
                }
            }
        }
        .onAppear { ptsFocused = true }
        .sheetMedium()
    }
}

/// A survey card's form (round 3 #1): rendered from the server's schema, so a
/// new or changed survey needs no app rebuild. Values go back as strings
/// keyed by field, exactly what the CLI form's writer receives.
struct DtdFormSheet: View {
    let form: DtdForm
    var onSubmit: ([String: String]) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var values: [String: String] = [:]

    var body: some View {
        NavigationStack {
            Form {
                ForEach(form.fields) { f in
                    Section {
                        field(f)
                    } header: {
                        Text(f.label).font(Theme.monoSmall)
                    } footer: {
                        if !f.help.isEmpty { Text(f.help).font(Theme.monoSmall) }
                    }
                }
            }
            .font(Theme.mono)
            .navigationTitle(form.title)
            .keyRowToolbar()   // same KeyRow as the edit sheet (2026-10-25)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("save") { onSubmit(values); dismiss() }
                }
            }
        }
        .onAppear {
            if values.isEmpty {
                values = Dictionary(uniqueKeysWithValues: form.fields.map { ($0.key, $0.value) })
            }
        }
        .sheetLarge()
    }

    private func binding(_ k: String) -> Binding<String> {
        Binding(get: { values[k] ?? "" }, set: { values[k] = $0 })
    }

    @ViewBuilder private func field(_ f: DtdForm.Field) -> some View {
        switch f.type {
        case "multiline":
            TextField(f.label, text: binding(f.key), axis: .vertical).lineLimit(2...8)
        case "int":
            // numbersAndPunctuation, not numberPad: some 0s numbers (x̄ mean)
            // take decimals and the server accepts them.
            TextField(f.label, text: binding(f.key))
                #if os(iOS)
                .keyboardType(.numbersAndPunctuation)
                #endif
        case "choice":
            Picker(f.label, selection: binding(f.key)) {
                Text("—").tag("")
                ForEach(f.options, id: \.self) { Text($0).tag($0) }
            }
        case "scale":
            // 1…N tap row (a 1-7 rating reads faster as buttons than a slider).
            let lo = f.min ?? 1, hi = max(f.max ?? 7, lo)
            HStack(spacing: 6) {
                ForEach(lo...hi, id: \.self) { n in
                    let on = values[f.key] == String(n)
                    Button("\(n)") { values[f.key] = on ? "" : String(n) }
                        .font(Theme.mono).buttonStyle(.bordered)
                        .tint(on ? Theme.accent : Theme.dim)
                }
            }
        default:
            TextField(f.label, text: binding(f.key))
        }
    }
}
