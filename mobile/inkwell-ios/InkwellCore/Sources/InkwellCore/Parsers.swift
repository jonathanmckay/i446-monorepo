import Foundation

public enum Parsers {
    public static func tasks(_ json: JSON) -> [DtdTask] {
        json.objects("tasks").map { t in
            let raw = t.string("raw")
            let title = t.string("title")
            return DtdTask(
                id: t.string("id"),
                raw: raw,
                title: title.trimmingCharacters(in: .whitespaces).isEmpty ? raw : title,
                est: DtdTask.resolveEst(serverEst: t.intOrNil("est"), raw: raw, domain: t.string("domain")),
                points: t.intOrNil("points"),
                color: t.string("color", "#e6e6e0"),
                domain: t.string("domain"),
                recurring: t.bool("recurring"),
                // The server sends the prompt LABEL ("CPAP quality (1-3)") or
                // null, never a Bool; t.bool read the label as NSString.boolValue
                // = false, so no card ever asked its value (2026-10-25: cpap).
                variablePrompt: !variableLabel(t).isEmpty || (t["variablePrompt"].raw as? Bool ?? false),
                scheduledTime: DtdTask.parseScheduled(t.string("scheduled")),
                agent: t.bool("agent"),
                running: t.bool("running"),
                form: t.string("form"),
                agentDone: t.bool("agentDone"),
                variableLabel: variableLabel(t))
        }
    }

    private static func variableLabel(_ t: JSON) -> String {
        ((t["variablePrompt"].raw as? String) ?? "").trimmingCharacters(in: .whitespaces)
    }

    public static func summary(_ json: JSON) -> DtdSummary {
        let s = json.has("summary") ? json["summary"] : json
        return DtdSummary(points: s.int("points"), done: s.int("done"))
    }

    public static func delayOptions(_ json: JSON) -> DelayOptions {
        let blocks = json.objects("blocks").map { DelayOptions.Block(glyph: $0.string("glyph"), hour: $0.int("hour")) }
        let minutes = (json["minutes"].array ?? []).compactMap { ($0 as? NSNumber)?.intValue }
        return DelayOptions(blocks: blocks, minutes: minutes)
    }

    public static func timeline(_ json: JSON) -> Timeline {
        let rows = json.objects("rows").map { r -> TimelineRow in
            var row = TimelineRow(type: r.string("type"))
            // Dividers read as the CLI's "卯:00" (the server sends the block name).
            let block = r.string("block")
            row.label = block.isEmpty ? r.string("label") : block + ":00"
            row.start = r.string("start")
            row.end = r.string("end")
            row.minutes = r.int("minutes")
            row.id = r.string("id")           // Toggl ids arrive as JSON numbers → same digits as a string
            row.desc = r.string("desc")
            row.project = r.string("project")
            row.color = r.string("color")
            row.tags = r.strings("tags")
            row.running = r.bool("running")
            row.logged = r.bool("logged")
            row.points = r.intOrNil("points")
            row.previewPoints = r.intOrNil("preview_points")
            row.title = r.string("title")
            row.isPast = r.bool("is_past")
            row.started = r.bool("started")
            row.startIso = r.string("start_iso")
            row.endIso = r.string("end_iso")
            row.rawTitle = r.string("raw_title")
            row.eventId = r.string("event_id")
            row.calendarId = r.string("calendar_id")
            return row
        }
        return Timeline(rows: rows, trackedMin: json.int("tracked_min"), points: json.intOrNil("points"),
                        date: json.string("date"),
                        isToday: json.has("is_today") ? json.bool("is_today") : true)
    }

    public static func projectCodes(_ json: JSON) -> [String] { json.strings("codes") }

    /// GET /api/form → the card's form, or nil when it has none ({form: null}).
    public static func form(_ json: JSON) -> DtdForm? {
        let name = json.string("form")
        guard !name.isEmpty else { return nil }
        let fields = json.objects("fields").map { f in
            DtdForm.Field(key: f.string("key"), label: f.string("label", f.string("key")),
                          type: f.string("type", "text"), options: f.strings("options"),
                          min: f.intOrNil("min"), max: f.intOrNil("max"),
                          value: f.string("value"), help: f.string("help"))
        }
        let cond = (json["closesIf"].raw as? [String: Any] ?? [:]).mapValues { JSON($0).string }
        return DtdForm(name: name, title: json.string("title", name), fields: fields,
                       keepsCard: json.bool("keepsCard"), closesIf: cond)
    }
}
