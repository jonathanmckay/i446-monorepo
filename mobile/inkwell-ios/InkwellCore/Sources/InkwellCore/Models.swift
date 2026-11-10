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
    public let scheduledTime: Date?   // Todoist due datetime when the task carries a time-of-day → hidden until it arrives (later-today defer)
    public let agent: Bool            // a Claude session is mid-turn on it (/claim) → 😈, grey
    public let running: Bool          // its Toggl timer is the live one (dtd Enter / janus /d) → ▶
    public let agentDone: Bool        // the claiming agent thinks it's finished → ✅ on the left (2026-10-22)
    public let form: String           // survey card (0s, 1s, xk887, …): opens a form instead of a timer; "" = none
    public let variableLabel: String  // the server's prompt text ("CPAP quality (1-3)"), "" if none (2026-10-25)

    public init(id: String, raw: String, title: String, est: Int?, points: Int?, color: String,
                domain: String, recurring: Bool, variablePrompt: Bool, scheduledTime: Date? = nil,
                agent: Bool = false, running: Bool = false, form: String = "", agentDone: Bool = false,
                variableLabel: String = "") {
        self.id = id; self.raw = raw; self.title = title; self.est = est; self.points = points
        self.color = color; self.domain = domain; self.recurring = recurring; self.variablePrompt = variablePrompt
        self.scheduledTime = scheduledTime; self.agent = agent; self.running = running; self.form = form
        self.agentDone = agentDone; self.variableLabel = variableLabel
    }

    /// A task is "active" when it's the live timer (▶) or a Claude session has
    /// claimed it (😈). Active rows float to the top of dtd.
    public var isActive: Bool { running || agent }

    /// Added on the phone while offline; no Todoist id until its add syncs.
    public var isLocal: Bool { id.hasPrefix("local-") }

    /// Copy with a flipped `running` flag — fields are `let`, so optimistic
    /// "this is now the live timer" updates rebuild the row.
    public func withRunning(_ v: Bool) -> DtdTask {
        DtdTask(id: id, raw: raw, title: title, est: est, points: points, color: color,
                domain: domain, recurring: recurring, variablePrompt: variablePrompt,
                scheduledTime: scheduledTime, agent: agent, running: v, form: form, agentDone: agentDone,
                variableLabel: variableLabel)
    }

    /// True when the task is deferred to later TODAY: it has a time-of-day and
    /// that time is still in the future on the current calendar day. dtd hides
    /// these until their time arrives (same contract as a block/minute snooze);
    /// a date-only due (scheduledTime == nil) is never hidden this way, and a
    /// time already past today stays visible. Bug 2026-10-22: later-today tasks
    /// were showing immediately because the client had no time field to test.
    public func deferredToLaterToday(_ now: Date, calendar: Calendar = .current) -> Bool {
        guard let t = scheduledTime else { return false }
        return t > now && calendar.isDate(t, inSameDayAs: now)
    }

    /// Drop tasks deferred to later today — what the dtd list should show now.
    public static func visible(_ list: [DtdTask], now: Date = Date(), calendar: Calendar = .current) -> [DtdTask] {
        list.filter { !$0.deferredToLaterToday(now, calendar: calendar) }
    }

    /// The terminal's ctrl-t views (tools/did/dtd.sh, 2026-07-14) on the phone
    /// (2026-11-08): standard = server order; project groups by domain
    /// (untagged last); time puts the shortest (N) first (no estimate last).
    /// Active rows (▶/😈) stay pinned on top in every view — on a phone the
    /// live task must never sort below the fold. Ties keep the server order,
    /// via an explicit index rather than relying on sort stability.
    public static func ordered(_ list: [DtdTask], by sort: DtdSort) -> [DtdTask] {
        if sort == .standard { return list }
        func key(_ t: DtdTask) -> (Int, String, Int) {
            switch sort {
            case .project: return (t.domain.isEmpty ? 1 : 0, t.domain, 0)
            case .time: return (t.est == nil ? 1 : 0, "", t.est ?? 0)
            case .standard: return (0, "", 0)
            }
        }
        return list.enumerated().sorted { a, b in
            if a.element.isActive != b.element.isActive { return a.element.isActive }
            let ka = key(a.element), kb = key(b.element)
            if ka != kb { return ka < kb }
            return a.offset < b.offset
        }.map(\.element)
    }
}

/// dtd list order, cycled by the filter-bar button (tap) or ctrl-t; long-press
/// picks one directly. Not persisted: every launch starts in standard, like
/// the terminal's `echo default > "$DTD_VIEW"`.
public enum DtdSort: CaseIterable, Hashable {
    case standard, project, time

    public var next: DtdSort {
        let all = Self.allCases
        return all[(all.firstIndex(of: self)! + 1) % all.count]
    }
    public var glyph: String {
        switch self { case .standard: "≡"; case .project: "▦"; case .time: "⏱" }
    }
    public var label: String {
        switch self { case .standard: "default"; case .project: "project"; case .time: "time · short first" }
    }
}

extension DtdTask {

    /// Parse a server `scheduled` string into a Date, but only when it carries a
    /// time-of-day (an ISO datetime with a `T`). A bare `YYYY-MM-DD` means "due
    /// today, no specific time" → nil, so it is never treated as a later-today
    /// defer. Accepts Z / ±offset (absolute instant) and floating local
    /// datetimes (no zone → parsed in the current time zone).
    public static func parseScheduled(_ s: String) -> Date? {
        guard s.contains("T") else { return nil }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = iso.date(from: s) { return d }
        iso.formatOptions = [.withInternetDateTime]
        if let d = iso.date(from: s) { return d }
        // Floating datetime with no zone (e.g. "2026-10-22T15:00:00"): local.
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = .current
        for fmt in ["yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm"] {
            f.dateFormat = fmt
            if let d = f.date(from: s) { return d }
        }
        return nil
    }

    /// Row prefix, same order as the web page: ▶ running · 😈 agent · ↻ recurring.
    public var prefix: String {
        (agentDone ? "✅ " : "") + (running ? "▶ " : "") + (agent ? "😈 " : "") + (recurring ? "↻ " : "")
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
    /// What a log would credit (server `preview_points`, incl. the 25→30
    /// meeting rounding); parsed but no longer shown (2026-10-25).
    public var previewPoints: Int? = nil
    /// 分 actually credited once logged (server `logged_points`, 2026-10-26).
    public var loggedPoints: Int? = nil
    // event
    public var title: String = ""
    public var isPast: Bool = false
    public var started: Bool = false
    public var startIso: String = ""
    public var endIso: String = ""
    // event delete (2026-10-25): the calendar's own title + Google ids
    public var rawTitle: String = ""
    public var eventId: String = ""
    public var calendarId: String = ""

    public init(type: String) { self.type = type }

    public var isEntry: Bool { type == "entry" }
    /// A timer started on the phone while offline (id "local-…"), not in Toggl yet.
    public var isLocal: Bool { id.hasPrefix("local-") }
    /// The optimistic just-started timer (2026-10-25), shown until the reload
    /// brings the real Toggl entry; it has no id the server knows, so inert.
    public var isPending: Bool { id.hasPrefix("pending-") }
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
    public var canSwipeLeft: Bool { isEntry || isEvent }

    /// What the right-swipe backing says it will do — the web's swipe label.
    public var rightLabel: String {
        if isEvent { return isPast ? "log 分 (\(minutes)m)" : (started ? "resume tracking" : "start tracking") }
        if isGap { return "fill" }
        // No 分 estimate on the label (2026-10-25: "should not have estimate
        // of fen"); the server still resolves the credit when it fires.
        return running ? "done (stop + log)" : "log 分"
    }

    /// Display line, as the Android adapter renders it.
    public var displayTitle: String {
        if isGap { return "· empty ·" }
        if isEvent { return "📅 " + title }
        let prefix = running ? "▶ " : (logged ? "✓ " : "")
        let t = tags.isEmpty ? "" : "  #" + tags.joined(separator: " #")
        return prefix + desc + t
    }
    /// Left column: start time only (2026-10-22, janus note: "time should
    /// just show start time only"); the end is the next row's start.
    public var displayMeta: String { start }

    /// Right-justified column, as the terminal janus renders a row
    /// (_entry_dur_display, JM 2026-11-07 "the log to work like it does in
    /// cli"): the duration, then " [N]" with the 分 credited once the entry
    /// is logged. An unlogged row shows its duration only: no would-be
    /// value, neither the resolved task [N] nor a "~N分" preview.
    public var rightMeta: String {
        if isEntry && logged, let p = loggedPoints { return "\(minutes)m [\(p)]" }
        return "\(minutes)m"
    }
}

/// One chip of the janus habit strip (2026-11-10): the server resolves the
/// text and fill, the phone just draws it. Line 1 = pending 0₦ habit names,
/// YTD standings owed, "ص N"; line 2 = Daily Dozen behind, "hcb ±N", "kcal N".
public struct HabitChip: Equatable, Hashable {
    public let text: String
    public let bg: String        // "#rrggbb"
    public init(text: String, bg: String) { self.text = text; self.bg = bg }
}

public struct Timeline: Equatable {
    public let rows: [TimelineRow]
    public let trackedMin: Int
    public let points: Int?
    public let date: String
    /// Whether `date` is the live day. False on a back/forward (?offset=) view,
    /// which is read-only. Defaults true so an old cached payload (always
    /// today's) and any non-server construction read as today.
    public let isToday: Bool
    /// The habit strip's non-empty lines (server `habits.line1/line2`), only
    /// on today's full pass; empty on the lite pass, a past day, or a failed
    /// Excel read, and the view draws nothing then.
    public let habits: [[HabitChip]]
    public init(rows: [TimelineRow], trackedMin: Int, points: Int?, date: String, isToday: Bool = true,
                habits: [[HabitChip]] = []) {
        self.rows = rows; self.trackedMin = trackedMin; self.points = points
        self.date = date; self.isToday = isToday; self.habits = habits
    }
    /// The lite pass carries no strip; keep the one already on screen for the
    /// same day so a cold load / day switch doesn't blank it between passes.
    public func carryingHabits(from prior: Timeline?) -> Timeline {
        guard habits.isEmpty, let p = prior, p.date == date, !p.habits.isEmpty else { return self }
        return Timeline(rows: rows, trackedMin: trackedMin, points: points, date: date,
                        isToday: isToday, habits: p.habits)
    }
}

/// A survey card's form (round 3 #1, 2026-10-22: "survey tasks doable in dtd
/// ios like they are in cli"), as served by dtd's GET /api/form. Rendered
/// generically on the phone; submitted as {key: string} to POST /api/form.
public struct DtdForm: Identifiable, Equatable {
    public struct Field: Equatable, Identifiable {
        public var id: String { key }
        public let key: String
        public let label: String
        public let type: String        // text | multiline | int | choice | scale
        public let options: [String]
        public let min: Int?
        public let max: Int?
        public let value: String
        public let help: String
        public init(key: String, label: String, type: String, options: [String] = [], min: Int? = nil,
                    max: Int? = nil, value: String = "", help: String = "") {
            self.key = key; self.label = label; self.type = type; self.options = options
            self.min = min; self.max = max; self.value = value; self.help = help
        }
    }
    public var id: String { name }
    public let name: String
    public let title: String
    public let fields: [Field]
    /// Server's close rule (2026-10-25): 1s / moralaudit keep their card;
    /// 0s closes 0l only when closesIf (points_checked = 1) holds.
    public let keepsCard: Bool
    public let closesIf: [String: String]
    public init(name: String, title: String, fields: [Field], keepsCard: Bool = false,
                closesIf: [String: String] = [:]) {
        self.name = name; self.title = title; self.fields = fields
        self.keepsCard = keepsCard; self.closesIf = closesIf
    }

    /// Saving with these answers closes the card → the row may leave at once.
    public func closesCard(_ values: [String: String]) -> Bool {
        !keepsCard && closesIf.allSatisfy { k, want in
            (values[k] ?? "").trimmingCharacters(in: .whitespaces) == want }
    }
}
