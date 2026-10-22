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
                variablePrompt: t.bool("variablePrompt"),
                scheduledTime: DtdTask.parseScheduled(t.string("scheduled")),
                agent: t.bool("agent"),
                running: t.bool("running"))
        }
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
            row.title = r.string("title")
            row.isPast = r.bool("is_past")
            row.started = r.bool("started")
            row.startIso = r.string("start_iso")
            row.endIso = r.string("end_iso")
            return row
        }
        return Timeline(rows: rows, trackedMin: json.int("tracked_min"), points: json.intOrNil("points"),
                        date: json.string("date"))
    }

    public static func projectCodes(_ json: JSON) -> [String] { json.strings("codes") }
}
