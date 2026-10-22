import Foundation

/// One row of dtd's /api/tasks.
public struct DtdTask: Identifiable, Equatable, Hashable {
    public let id: String
    public let raw: String            // full Todoist content incl. (N)/[N]/{N} — what /api/done and /api/start take
    public let title: String          // annotation-stripped display text
    public let est: Int?              // (N) minutes
    public let points: Int?           // [N] or {N}
    public let color: String          // "#rrggbb" per domain, resolved server-side
    public let domain: String
    public let recurring: Bool
    public let variablePrompt: Bool   // ask for a value before completing (variable habits)
    public let agent: Bool            // a Claude session is mid-turn on it (/claim) → 😈, grey
    public let running: Bool          // its Toggl timer is the live one (dtd Enter / janus /d) → ▶

    public init(id: String, raw: String, title: String, est: Int?, points: Int?, color: String,
                domain: String, recurring: Bool, variablePrompt: Bool, agent: Bool = false, running: Bool = false) {
        self.id = id; self.raw = raw; self.title = title; self.est = est; self.points = points
        self.color = color; self.domain = domain; self.recurring = recurring; self.variablePrompt = variablePrompt
        self.agent = agent; self.running = running
    }

    /// A task is "active" when it's the live timer (▶) or a Claude session has
    /// claimed it (😈). Active rows float to the top of dtd.
    public var isActive: Bool { running || agent }

    /// Copy with a flipped `running` flag — fields are `let`, so optimistic
    /// "this is now the live timer" updates rebuild the row.
    public func withRunning(_ v: Bool) -> DtdTask {
        DtdTask(id: id, raw: raw, title: title, est: est, points: points, color: color,
                domain: domain, recurring: recurring, variablePrompt: variablePrompt,
                agent: agent, running: v)
    }

    /// Row prefix, same order as the web page: ▶ running · 😈 agent · ↻ recurring.
    public var prefix: String {
        (running ? "▶ " : "") + (agent ? "😈 " : "") + (recurring ? "↻ " : "")
    }

    /// Right-justified "(time) [value]" like the terminal and the web.
    public var meta: String {
        [est.map { "(\($0))" }, points.map { "[\($0)]" }].compactMap { $0 }.joined(separator: " ")
    }

    /// Fallback (N) minutes for domains whose tasks routinely ship without an
    /// explicit time annotation. dtd/web leave these blank, so the phone shows
    /// no time estimate at all (bug 2026-10-22: hcmc/i444 rows had no `(N)`).
    /// These are variable/consumption domains — media (hcmc) and the i444
    /// count habit — where the user enters the real minutes on completion;
    /// the default just gives the row a sensible placeholder estimate.
    public static let domainDefaultEst: [String: Int] = [
        "hcmc": 30, "hcmc2": 30, "i444": 15,
    ]

    /// Resolve a task's display estimate: the server's `(N)`, else the `(N)`
    /// re-parsed from `raw` (in case the server dropped it), else the domain
    /// default. Keeps completion payloads untouched — this is display-only.
    public static func resolveEst(serverEst: Int?, raw: String, domain: String) -> Int? {
        if let e = serverEst { return e }
        if let r = minutesInRaw(raw) { return r }
        return domainDefaultEst[domain]
    }

    /// First `(N)` minutes literal in a raw Todoist content string, if any.
    static func minutesInRaw(_ raw: String) -> Int? {
        guard let m = raw.range(of: #"\(\d+\)"#, options: .regularExpression) else { return nil }
        return Int(raw[m].dropFirst().dropLast())
    }
}

public struct DtdSummary: Equatable {
    public var points: Int
    public var done: Int
    public init(points: Int, done: Int) { self.points = points; self.done = done }
}

public struct DelayOptions: Equatable {
    public struct Block: Equatable {
        public let glyph: String
        public let hour: Int
        public init(glyph: String, hour: Int) { self.glyph = glyph; self.hour = hour }
    }
    public let blocks: [Block]
    public let minutes: [Int]
    public init(blocks: [Block], minutes: [Int]) { self.blocks = blocks; self.minutes = minutes }
    public static let empty = DelayOptions(blocks: [], minutes: [])
    public var isEmpty: Bool { blocks.isEmpty && minutes.isEmpty }
}

/// One row of janus's /api/timeline. type ∈ divider | gap | entry | event.
public struct TimelineRow: Equatable, Hashable {
    public var type: String
    public var label: String = ""        // divider
    public var start: String = ""        // HH:MM (entry/gap/event)
    public var end: String = ""          // HH:MM or "now" for the running entry
    public var minutes: Int = 0
    // entry
    public var id: String = ""
    public var desc: String = ""
    public var project: String = ""
    public var color: String = ""
    public var tags: [String] = []
    public var running: Bool = false
    public var logged: Bool = false
    public var points: Int? = nil
    // event
    public var title: String = ""
    public var isPast: Bool = false
    public var started: Bool = false
    public var startIso: String = ""
    public var endIso: String = ""

    public init(type: String) { self.type = type }

    public var isEntry: Bool { type == "entry" }
    public var isGap: Bool { type == "gap" }
    public var isEvent: Bool { type == "event" }
    public var isDivider: Bool { type == "divider" }

    /// Right-swipe (commit) allowed? Mirrors the web: an already-logged entry
    /// is inert so a reload never invites a re-swipe; dividers do nothing.
    public var canSwipeRight: Bool {
        switch type {
        case "entry": return running || !logged
        case "gap", "event": return true
        default: return false
        }
    }
    /// Left-swipe (edit/split) is for Toggl entries only — a gap or calendar
    /// event isn't a Toggl entry yet.
    public var canSwipeLeft: Bool { isEntry }

    /// What the right-swipe backing says it will do — the web's swipe label.
    public var rightLabel: String {
        if isEvent { return isPast ? "log 分 (\(minutes)m)" : (started ? "resume tracking" : "start tracking") }
        if isGap { return "fill" }
        if running { return points.map { "done ✓ (\($0)分)" } ?? "done ✓ (stop + log)" }
        return points.map { "log \($0)分" } ?? "neon log 分 (\(minutes)m)"
    }

    /// Display line, as the Android adapter renders it.
    public var displayTitle: String {
        if isGap { return "· empty ·" }
        if isEvent { return "📅 " + title }
        let prefix = running ? "▶ " : (logged ? "✓ " : "")
        let t = tags.isEmpty ? "" : "  #" + tags.joined(separator: " #")
        return prefix + desc + t
    }
    public var displayMeta: String {
        "\(start)–\(end) · \(minutes)m" + (points.map { " · \($0)分" } ?? "")
    }
}

public struct Timeline: Equatable {
    public let rows: [TimelineRow]
    public let trackedMin: Int
    public let points: Int?
    public let date: String
    public init(rows: [TimelineRow], trackedMin: Int, points: Int?, date: String) {
        self.rows = rows; self.trackedMin = trackedMin; self.points = points; self.date = date
    }
}
