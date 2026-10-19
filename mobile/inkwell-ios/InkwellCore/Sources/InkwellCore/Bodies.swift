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
    public static func fill(desc: String, start: String, end: String) -> [String: Any] {
        ["desc": desc, "start": start, "end": end]
    }
    public static func edit(id: String, desc: String, start: String, end: String, project: String) -> [String: Any] {
        ["id": id, "desc": desc, "start": start, "end": end, "project": project]
    }
    public static func split(id: String, mode: String) -> [String: Any] { ["id": id, "mode": mode] }
}
