import Foundation

/// Request bodies, byte-for-byte what the web pages (and the Android client) send.
public enum Bodies {
    /// /api/done. A variable habit's typed value rides in the content, exactly
    /// as the web's submitVal does (title + " " + value); blank = skip.
    public static func done(_ t: DtdTask, value: String? = nil) -> [String: Any] {
        let v = (value ?? "").filter { $0.isNumber }
        let content = v.isEmpty ? t.raw : "\(t.title) \(v)"
        return ["id": t.id, "content": content]
    }
    public static func start(_ t: DtdTask) -> [String: Any] { ["content": t.raw] }
    public static func byId(_ t: DtdTask) -> [String: Any] { ["id": t.id] }
    public static func delayHour(_ t: DtdTask, hour: Int) -> [String: Any] { ["id": t.id, "hour": hour] }
    /// days 0 = next occurrence (recurring) / one day (one-off), N = N days out.
    public static func delayDays(_ t: DtdTask, days: Int) -> [String: Any] { ["id": t.id, "days": days] }
    public static func delayMinutes(_ t: DtdTask, minutes: Int) -> [String: Any] { ["id": t.id, "minutes": minutes] }
    public static func add(_ content: String) -> [String: Any] { ["content": content] }

    public static func log(_ r: TimelineRow) -> [String: Any] {
        ["id": r.id, "desc": r.desc, "minutes": r.minutes, "project": r.project, "tags": r.tags]
    }
    public static func doneCurrent(_ r: TimelineRow) -> [String: Any] {
        ["id": r.id, "desc": r.desc, "project": r.project]
    }
    public static func convertEvent(_ r: TimelineRow) -> [String: Any] {
        ["title": r.title, "start_iso": r.startIso, "end_iso": r.endIso, "code": r.project, "is_past": r.isPast]
    }
    /// `project` is optional: the server also honours a trailing `@code` in
    /// the description, exactly like /tg.
    public static func fill(desc: String, start: String, end: String, project: String = "") -> [String: Any] {
        var b: [String: Any] = ["desc": desc, "start": start, "end": end]
        if !project.isEmpty { b["project"] = project }
        return b
    }
    public static func edit(id: String, desc: String, start: String, end: String, project: String) -> [String: Any] {
        ["id": id, "desc": desc, "start": start, "end": end, "project": project]
    }
    public static func split(id: String, mode: String) -> [String: Any] { ["id": id, "mode": mode] }
    /// /api/delete-event: Google-hosted → deleted from the calendar; Outlook → hidden.
    public static func deleteEvent(_ r: TimelineRow) -> [String: Any] {
        ["title": r.rawTitle.isEmpty ? r.title : r.rawTitle, "start_iso": r.startIso, "end_iso": r.endIso,
         "event_id": r.eventId, "calendar_id": r.calendarId]
    }
}
