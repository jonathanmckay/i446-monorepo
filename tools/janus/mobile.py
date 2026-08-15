#!/usr/bin/env python3
"""
janus mobile — iPhone-first mirror of the `janus` timeline TUI.

Same data source (Toggl + gcal/outlook), same domain colors, same 地支 block
structure — as a swipeable web list of today's timeline. Right-swipe actions
(the ⌥↵ desktop gesture, ported piecemeal by row type):

  · UNTRACKED GAP    → dialog with start/end prefilled; saving creates the
    Toggl entry (optional @code picks the project, like /tg).
  · TIME ENTRY       → logs its minutes through the real /did (did-fast.py
    "<desc> <minutes> [@code]"), which routes exactly like the desktop: 0n
    habit → minutes to its 0n column; variable/1n+ → base+rate; Todoist
    word-overlap match → its [N]; otherwise the variable path writes
    minutes-as-points to the inferred domain column + a posthoc task. A
    per-day ledger prevents double-logging the same entry.
  · CALENDAR EVENT   → not yet ended: starts a live tg-fast timer (backdated
    if the meeting already began). Already ended: backfills a completed
    Toggl entry AND grants its points in one did-fast call. Events already
    covered/tracked by a real Toggl entry never show (2026-08-14).
  · RUNNING TIMER (the pinned open entry) → stops it AND grants its points
    in one shot (did-fast's completed-range trim_range() closes the still-
    open entry itself — mirrors desktop's _run_current_timer_done, minus
    its d357-recording-finalize branch and interactive "no resolvable
    points" prompt, both left to the desktop timer per 2026-08-14 request).

Left-swipe an entry to edit/split (desc/time/project) — gaps, events, and
the running row have nothing left-swipeable.

Run:   python3 mobile.py           (binds 0.0.0.0:5561)
Open:  http://ix:5561              (from the phone, same as dtd/dashboard)
"""
from __future__ import annotations

import concurrent.futures
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

# Toggl API key: env first, else the MCP config (same fallback toggl_cli uses —
# a launchd agent has a bare environment).
def _load_api_key() -> str:
    try:
        d = json.loads((Path.home() / ".claude.json").read_text())
        return (d.get("mcpServers", {}).get("toggl_server", {})
                 .get("env", {}).get("TOGGL_API_KEY", ""))
    except Exception:
        return ""

if not os.environ.get("TOGGL_API_KEY"):
    os.environ["TOGGL_API_KEY"] = _load_api_key()
os.environ.setdefault("TOGGL_WORKSPACE_ID", "2092616")

sys.path.insert(0, str(Path.home() / "i446-monorepo"))
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
sys.path.insert(0, str(Path.home() / "i446-monorepo/tools/tg"))

from flask import Flask, jsonify, render_template_string, request  # noqa: E402

from mcp.toggl_server import toggl_api  # noqa: E402
from mcp.toggl_server.config import PROJECT_MAP, PROJECT_NAMES  # noqa: E402

# Calendar clients (2026-08-14): both self-contained (gcal_client reads OAuth
# tokens straight off disk, no MCP server needed at runtime; outlook_client
# needs agency_mcp from tools/ibx/, already optional inside outlook_client
# itself). Neither import may take the whole daemon down — wrap both, not
# just outlook's (a missing googleapiclient in this venv used to be able to
# kill every timeline request, calendar-related or not).
try:
    import gcal_client  # noqa: E402
except Exception as _e:  # noqa: BLE001
    gcal_client = None
    print("WARN gcal_client import failed:", _e, file=sys.stderr)
try:
    import outlook_client  # noqa: E402
except Exception as _e:  # noqa: BLE001
    outlook_client = None
    print("WARN outlook_client import failed:", _e, file=sys.stderr)

PORT = 5561
TZ = ZoneInfo("America/Los_Angeles")
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
TG_FAST = Path.home() / "i446-monorepo/tools/tg/tg-fast.py"
STATE_DIR = Path.home() / ".local/state/jm"
MIN_GAP_MIN = 5          # gaps shorter than this are not shown
DAY_START_HOUR = 0       # timeline from midnight (睡觉 entries live there)

# Calendar → project resolution — mirrors tools/tg/janus.py's own copies
# (CALENDAR_PROJECT_MAP / EVENT_KEYWORDS / gcal_project_code) verbatim.
# Not imported directly: janus.py is a prompt_toolkit TUI whose top-level
# code builds an Application/key-bindings at import time — importing it into
# this Flask daemon would try to do all of that too.
CALENDAR_PROJECT_MAP = {
    "m5x2 Cal": "m5x2",
    "3494 House": "m5x2",
    "CAIS School": "xk87",
    "Habits": "hcm",
    "lx@m5c7.com": "xk88",
    "lxu888": "xk88",
    "Calendar": "infra",
    "jonathan.b.mckay@gmail.com": "infra",
    "Outlook": "i9",
}
EVENT_KEYWORDS = [
    (["1:1", "1|1", "standup", "sprint", "retro", "slt", "metrics"], "i9"),
    (["m5x2", "property", "tenant", "lease", "appfolio"], "m5x2"),
    (["school", "cais", "pta", "ptc"], "xk87"),
    (["bball", "basketball", "gym", "hiit"], "hcbp"),
]

# Neon domain palette — mirrors tools/dtd/dtd.py / tools/did/dtd.sh COLORS.
COLORS = {
    "g245": "#00e676", "epcn": "#00bfa5", "s897": "#1b5e20", "hcmc2": "#ffd600",
    "xk87": "#fd6c1d", "xk88": "#e65100", "hci": "#63ede0", "i9": "#2979ff",
    "n156": "#1249b4", "hcmc": "#0d3b66", "m5x2": "#d50032", "m828": "#9b0023",
    "hcb": "#f81d78",
    "hcbp": "#ff4081", "infra": "#9e9e9e", "i444": "#616161", "i447": "#a89c8a",
    "hcm": "#aa00ff", "hcmp": "#7c4dff", "hcmr": "#bda6ff", "家": "#ff4136",
    "睡觉": "#666666",
}
DEFAULT_COLOR = "#bdbdbd"

BLOCKS = [(4, "卯"), (6, "辰"), (8, "巳"), (10, "午"), (12, "未"),
          (14, "申"), (16, "酉"), (18, "戌"), (20, "亥"), (22, "子")]

_AT = re.compile(r"\s*@(\S+)")

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
import state_paths  # noqa: E402
TASK_QUEUE = state_paths.TASK_QUEUE
_POINTS_BRACKET_RE = re.compile(r"\[(\d+)\]")


def _clean_annotations(s: str) -> str:
    """Strip dtd/Todoist annotations: (30) time, [40] points, {60} estimate,
    and any trailing @project tag — leaving the bare task name. Mirrors
    tools/tg/janus.py's own copy."""
    s = re.sub(r"\s*\(\d+\)", "", s)
    s = re.sub(r"\s*\[\d+\]", "", s)
    s = re.sub(r"\s*\{\d+\}", "", s)
    s = re.sub(r"\s*@\S+", "", s)
    return s.strip()


def _norm_key(s: str) -> str:
    """Normalization key tolerant of dash/whitespace/case drift between the
    Toggl timer name and the task content. Mirrors janus.py's own copy and
    did-fast's _norm."""
    return re.sub(r"[\s\-—–]+", " ", _clean_annotations(s)).strip().lower()


def _resolvable_points(desc: str) -> int | None:
    """The points value did-fast would resolve for `desc` on its own — an
    inline [N] already in `desc` itself, or a [N] on a matching OPEN task in
    the cached task queue (same file did-fast's own Todoist-content lookup
    reads). None when neither exists, meaning did-fast would fall back to
    minutes-as-points (the variable-task path) rather than the task's real
    value. Mirrors tools/tg/janus.py's own _resolvable_points verbatim —
    2026-08-15: mobile's swipe-to-log lacked this pre-check entirely, so a
    dtd-started task worth e.g. [30] silently risked being credited by raw
    elapsed minutes instead, if did-fast's own fuzzy Todoist word-overlap
    match (Step 5, threshold 0.6/0.4) didn't independently re-find the same
    task. This exact-normalized-key match against the SAME cache dtd itself
    populates is strictly more reliable than re-deriving the match via fuzzy
    text search after the fact."""
    m = _POINTS_BRACKET_RE.search(desc or "")
    if m:
        return int(m.group(1))
    try:
        data = json.loads(TASK_QUEUE.read_text())
    except Exception:
        return None
    target = _norm_key(desc)
    if not target:
        return None
    found = None

    def walk(o):
        nonlocal found
        if found is not None:
            return
        if isinstance(o, dict):
            c = o.get("content")
            if c and _norm_key(c) == target:
                found = c
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    if not found:
        return None
    m = _POINTS_BRACKET_RE.search(found)
    return int(m.group(1)) if m else None


def _ledger_path(day: _dt.date) -> Path:
    return STATE_DIR / f"janus-mobile-logged-{day.isoformat()}.json"


def _ledger(day: _dt.date) -> dict:
    try:
        return json.loads(_ledger_path(day).read_text())
    except Exception:
        return {}


def _ledger_add(day: _dt.date, entry_id: str, note: str) -> None:
    d = _ledger(day)
    d[str(entry_id)] = note
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _ledger_path(day).with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    tmp.replace(_ledger_path(day))


def _parse_iso(s: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)


def _fetch_today() -> list[dict]:
    today = _dt.datetime.now(TZ).date()
    raw = toggl_api.get_entries(
        start_date=(today - _dt.timedelta(days=1)).isoformat(),
        end_date=(today + _dt.timedelta(days=1)).isoformat()) or []
    now = _dt.datetime.now(TZ)
    out = []
    for e in raw:
        try:
            st = _parse_iso(e["start"])
        except Exception:
            continue
        running = (e.get("duration") or 0) < 0 or not e.get("stop")
        en = now if running else _parse_iso(e["stop"])
        # keep the part that falls inside today
        if en.date() < today or st.date() > today:
            continue
        day_start = _dt.datetime.combine(today, _dt.time(0, 0), TZ)
        st_c, en_c = max(st, day_start), min(en, now)
        if en_c <= st_c:
            continue
        code = PROJECT_NAMES.get(e.get("project_id") or 0, "")
        out.append({
            "id": str(e.get("id")),
            "desc": e.get("description") or "(no description)",
            "project": code,
            "color": COLORS.get(code, DEFAULT_COLOR),
            "tags": e.get("tags") or [],
            "start": st_c, "end": en_c, "running": running,
        })
    out.sort(key=lambda r: r["start"])
    return out


def _safe_title(title: str) -> str:
    """did-fast.py and tg-fast.py both split multi-item input on
    [,;，；] — an event title with a comma ("CosmosDB Deprecation, Part 3")
    would otherwise silently become two bogus items (the exact bug
    tools/tg/janus.py's _safe_event_title fixed 2026-07-29). Mirrors that
    function verbatim."""
    return re.sub(r"\s+", " ", re.sub(r"[,;，；]", " ", title or "")).strip()


def gcal_project_code(event: dict) -> str:
    """Mirrors tools/tg/janus.py's gcal_project_code verbatim."""
    title_lower = (event.get("title") or "").lower()
    if "m5x2" in title_lower:
        return "m5x2"
    code = CALENDAR_PROJECT_MAP.get(event.get("calendar", ""))
    if code:
        return code
    for keywords, kw_code in EVENT_KEYWORDS:
        if any(kw in title_lower for kw in keywords):
            return kw_code
    return ""


def _norm_meeting_title(s: str) -> str:
    return _safe_title(s).lower()


TRACKED_EARLY_START_MIN = 15  # mirrors janus.py's own constant


def _event_covered(ev: dict, entries: list[dict]) -> bool:
    """True if ANY Toggl entry today overlaps this event's window at all —
    mirrors janus.py's _event_covered. Deliberately NOT porting janus.py's
    fuller _event_reclaimable (a covered-but-swallowed-by-a-runaway-clock
    recovery path) — out of scope for the mobile MVP; a covered event just
    stays hidden here rather than offering to reclaim it."""
    return any(e["start"] < ev["end_dt"] and e["end"] > ev["start_dt"] for e in entries)


def _event_tracked(ev: dict, entries: list[dict]) -> bool:
    """A same-titled Toggl entry is already tracking THIS event instance —
    mirrors janus.py's _event_tracked. Without this, an event you already
    started a running timer for up to TRACKED_EARLY_START_MIN early still
    shows as untracked; swiping it would fire an un-backdated tg-fast start
    that stops your real running timer and starts a duplicate."""
    t = _norm_meeting_title(ev.get("title") or "")
    if not t:
        return False
    for e in entries:
        if _norm_meeting_title(e.get("desc") or "") != t:
            continue
        if e["start"] < ev["end_dt"] and e["end"] > ev["start_dt"]:
            return True
        if (e.get("running")
                and 0 <= (ev["start_dt"] - e["start"]).total_seconds()
                <= TRACKED_EARLY_START_MIN * 60):
            return True
    return False


CAL_FETCH_TIMEOUT_SEC = 10  # per source; see _fetch_calendar_source
CAL_FAIL_COOLDOWN_SEC = 120  # after a timeout/failure, skip retrying this
# source for this long — outlook_client has been observed FAILING EVERY
# CALL from ix's daemon environment (2026-08-14), and this app's timeline
# reloads on every swipe action, so without a cooldown a known-broken
# source taxes every single page load with the full CAL_FETCH_TIMEOUT_SEC
# wait for nothing. Cleared immediately on the next success, so a source
# that recovers isn't held back by a stale cooldown.
_CAL_FAIL_UNTIL: dict[str, float] = {}


def _fetch_calendar_source(client_name: str, day_start: _dt.datetime,
                           day_end: _dt.datetime) -> list[dict]:
    """Fetch one calendar source (gcal_client or outlook_client) in a
    SEPARATE PROCESS with a hard kill-on-timeout.

    Discovered live (2026-08-14): outlook_client.list_events()'s own
    internal `mcp.call_tool(..., timeout=15)` does NOT reliably enforce that
    timeout from this daemon's environment — a direct call hung 25s+ with no
    error, which took the ENTIRE daemon down (its dev-server default is
    single-threaded, so one wedged request blocks every other endpoint
    behind it, gap-fill/log/edit included, not just calendar rows). A Python
    thread can't be forcibly killed once it's stuck in a hung network call,
    so a thread-based timeout would just abandon the hang in place — it
    would stop blocking THIS request but the leaked thread (and, at
    high-enough request volume, eventually every thread in the pool) stays
    stuck forever. A subprocess CAN be killed outright: subprocess.run's own
    `timeout=` reliably terminates the child, so a hang here costs at most
    CAL_FETCH_TIMEOUT_SEC and leaks nothing. Records/clears a cooldown on
    failure/success (see CAL_FAIL_COOLDOWN_SEC) — _fetch_calendar_raw is the
    one that actually SKIPS a source during its cooldown, so this function
    can still be called directly (e.g. from tests) without that gate."""
    script = (
        "import sys, json, datetime\n"
        f"import {client_name}\n"
        f"s = datetime.datetime.fromisoformat({day_start.isoformat()!r})\n"
        f"e = datetime.datetime.fromisoformat({day_end.isoformat()!r})\n"
        f"evs = {client_name}.list_events(s, e)\n"
        "for ev in evs:\n"
        "    ev['start_dt'] = ev['start_dt'].isoformat()\n"
        "    ev['end_dt'] = ev['end_dt'].isoformat()\n"
        "print(json.dumps(evs))\n"
    )
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", "-c", script],
            capture_output=True, text=True, timeout=CAL_FETCH_TIMEOUT_SEC,
            cwd=str(Path.home() / "i446-monorepo/tools/tg"))
    except subprocess.TimeoutExpired:
        print(f"WARN {client_name} fetch timed out after {CAL_FETCH_TIMEOUT_SEC}s",
              file=sys.stderr)
        _CAL_FAIL_UNTIL[client_name] = time.time() + CAL_FAIL_COOLDOWN_SEC
        return []
    if proc.returncode != 0:
        print(f"WARN {client_name} fetch failed:", proc.stderr.strip()[-300:], file=sys.stderr)
        _CAL_FAIL_UNTIL[client_name] = time.time() + CAL_FAIL_COOLDOWN_SEC
        return []
    try:
        raw = json.loads(proc.stdout)
    except Exception as e:
        print(f"WARN {client_name} fetch: bad output ({e}):", proc.stdout[:200], file=sys.stderr)
        _CAL_FAIL_UNTIL[client_name] = time.time() + CAL_FAIL_COOLDOWN_SEC
        return []
    for ev in raw:
        ev["start_dt"] = _parse_iso(ev["start_dt"])
        ev["end_dt"] = _parse_iso(ev["end_dt"])
    _CAL_FAIL_UNTIL.pop(client_name, None)
    return raw


def _fetch_calendar_raw(day_start: _dt.datetime, day_end: _dt.datetime) -> list[dict]:
    """Both calendar sources, fetched concurrently (each in its own
    subprocess — see _fetch_calendar_source) so a slow/hung one doesn't
    double the wait, deduped by (title, start, end) to collapse the same
    meeting mirrored across gcal and outlook. Best-effort: a source that's
    unavailable or fails just contributes nothing. A source still inside its
    post-failure cooldown (CAL_FAIL_COOLDOWN_SEC) is skipped outright — no
    subprocess spawned, no wait — rather than re-paying the full
    CAL_FETCH_TIMEOUT_SEC on every single timeline reload for a source
    that's already known to be down right now."""
    now = time.time()
    sources = [n for n, mod in (("gcal_client", gcal_client),
                                ("outlook_client", outlook_client))
              if mod is not None and _CAL_FAIL_UNTIL.get(n, 0) <= now]
    raw: list[dict] = []
    if sources:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as pool:
            futs = {pool.submit(_fetch_calendar_source, name, day_start, day_end): name
                   for name in sources}
            for fut in concurrent.futures.as_completed(futs):
                try:
                    raw += fut.result()
                except Exception as e:
                    print(f"WARN {futs[fut]} fetch:", e, file=sys.stderr)
    raw.sort(key=lambda e: e["start_dt"])
    seen = set()
    deduped = []
    for e in raw:
        k = ((e.get("title") or "").strip().lower(), e["start_dt"], e["end_dt"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(e)
    return deduped


def _filter_events(raw: list[dict], entries: list[dict]) -> list[dict]:
    """Raw calendar events → ones actually worth showing as a convertible
    row: not all-day, not transparent ("free" holds), not already
    covered/tracked by a real Toggl entry. Pure — no I/O — so it's testable
    without touching the calendar sources at all."""
    now = _dt.datetime.now(TZ)
    out = []
    for ev in raw:
        if ev.get("all_day") or ev.get("transparency") == "transparent":
            continue
        if _event_covered(ev, entries) or _event_tracked(ev, entries):
            continue
        code = gcal_project_code(ev)
        out.append({
            "title": _safe_title(ev.get("title") or "(no title)"),
            "start_dt": ev["start_dt"], "end_dt": ev["end_dt"],
            "code": code, "color": COLORS.get(code, DEFAULT_COLOR),
            "is_past": ev["end_dt"] <= now,
            "started": ev["start_dt"] <= now,
        })
    return out


def _fetch_events_today(entries: list[dict]) -> list[dict]:
    today = _dt.datetime.now(TZ).date()
    day_start = _dt.datetime.combine(today, _dt.time(0, 0), TZ)
    day_end = day_start + _dt.timedelta(days=1)
    return _filter_events(_fetch_calendar_raw(day_start, day_end), entries)


def _split_gaps_around_events(gaps: list[dict], events: list[dict]) -> list[dict]:
    """Carve each event's own window out of any gap it falls inside —
    mirrors tools/tg/janus.py's _split_gaps_around_events. Operates on plain
    (start_dt, end_dt) gap dicts (see _raw_gaps), same shape the caller
    builds."""
    if not events:
        return gaps
    out = []
    for g in gaps:
        segments = [(g["start_dt"], g["end_dt"])]
        for ev in events:
            next_segments = []
            for s, e in segments:
                ev_s, ev_e = max(ev["start_dt"], s), min(ev["end_dt"], e)
                if ev_s >= ev_e:
                    next_segments.append((s, e))
                    continue
                if ev_s > s:
                    next_segments.append((s, ev_s))
                if ev_e < e:
                    next_segments.append((ev_e, e))
            segments = next_segments
        out += [{"start_dt": s, "end_dt": e} for s, e in segments]
    return out


def _split_gap_at_boundaries(start_dt: _dt.datetime, end_dt: _dt.datetime,
                             day0: _dt.datetime) -> list[tuple]:
    """Split [start_dt, end_dt) at every 地支 block boundary it crosses, so
    a divider can land BETWEEN the pieces instead of the whole span
    rendering as one gap with dividers stacked before/after it."""
    cuts = sorted(day0 + _dt.timedelta(hours=h) for h, _ in BLOCKS
                 if start_dt < day0 + _dt.timedelta(hours=h) < end_dt)
    pts = [start_dt] + cuts + [end_dt]
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def build_timeline() -> dict:
    today = _dt.datetime.now(TZ).date()
    now = _dt.datetime.now(TZ)
    entries = _fetch_today()
    events = _fetch_events_today(entries)
    logged = _ledger(today)
    day0 = _dt.datetime.combine(today, _dt.time(0, 0), TZ)

    def hhmm(dt: _dt.datetime) -> str:
        return dt.strftime("%H:%M")

    # 1. Raw untracked spans from Toggl entries alone (events don't count as
    # "tracked" — the whole point of showing them is to nudge conversion).
    raw_gaps = []
    cursor = day0
    for e in entries:
        if e["start"] > cursor:
            raw_gaps.append({"start_dt": cursor, "end_dt": e["start"]})
        cursor = max(cursor, e["end"])
    if now > cursor:
        raw_gaps.append({"start_dt": cursor, "end_dt": now})

    # 2. Carve each event's window out of whatever gap it falls inside, then
    # 3. split what's left at 地支 boundaries, dropping anything now under
    # MIN_GAP_MIN.
    leaf_gaps = []
    for g in _split_gaps_around_events(raw_gaps, events):
        for s, e in _split_gap_at_boundaries(g["start_dt"], g["end_dt"], day0):
            mins = int((e - s).total_seconds() // 60)
            if mins >= MIN_GAP_MIN:
                leaf_gaps.append({"start_dt": s, "end_dt": e, "minutes": mins})

    # 4. Merge entries + leaf gaps + events into one time-ordered stream, then
    # walk it inserting a 地支 divider row wherever a block boundary is
    # crossed between one item and the next (uniform regardless of WHY
    # there's a time gap between them — entry→gap, gap→event, event→gap...).
    stream = ([{"kind": "gap", "start_dt": g["start_dt"], "data": g} for g in leaf_gaps]
             + [{"kind": "entry", "start_dt": e["start"], "data": e} for e in entries]
             + [{"kind": "event", "start_dt": ev["start_dt"], "data": ev} for ev in events])
    stream.sort(key=lambda it: it["start_dt"])

    rows: list[dict] = []
    tracked_min = 0
    bidx = 0

    def emit_dividers_before(t: _dt.datetime):
        nonlocal bidx
        while bidx < len(BLOCKS):
            h, name = BLOCKS[bidx]
            bdt = day0 + _dt.timedelta(hours=h)
            if bdt > t or bdt > now:
                break
            rows.append({"type": "divider", "label": f"{name} {h:02d}:00"})
            bidx += 1

    for it in stream:
        emit_dividers_before(it["start_dt"])
        if it["kind"] == "gap":
            g = it["data"]
            rows.append({"type": "gap", "start": hhmm(g["start_dt"]), "end": hhmm(g["end_dt"]),
                         "minutes": g["minutes"]})
        elif it["kind"] == "entry":
            e = it["data"]
            mins = int(round((e["end"] - e["start"]).total_seconds() / 60))
            tracked_min += mins
            rows.append({"type": "entry", "id": e["id"], "desc": e["desc"],
                         "project": e["project"], "color": e["color"],
                         "tags": e.get("tags") or [],
                         "start": hhmm(e["start"]), "end": ("now" if e["running"] else hhmm(e["end"])),
                         "minutes": mins, "running": e["running"],
                         "logged": e["id"] in logged,
                         # A resolved dtd-task point value, if this entry's
                         # description exact-matches one (see
                         # _resolvable_points) — lets the swipe label show
                         # what will ACTUALLY be credited (2026-08-15) rather
                         # than always implying "minutes = points".
                         "points": _resolvable_points(e["desc"])})
        else:  # event
            ev = it["data"]
            mins = int(round((ev["end_dt"] - ev["start_dt"]).total_seconds() / 60))
            rows.append({"type": "event", "title": ev["title"], "project": ev["code"],
                         "color": ev["color"], "start": hhmm(ev["start_dt"]),
                         "end": hhmm(ev["end_dt"]), "minutes": mins,
                         "is_past": ev["is_past"], "started": ev["started"],
                         "start_iso": ev["start_dt"].isoformat(),
                         "end_iso": ev["end_dt"].isoformat()})
    emit_dividers_before(now)

    # Σ points for the header (same source as mobile dtd: 0分 col D on Ix).
    points = None
    try:
        from neon import excel
        r = excel.read("0分", "D", date="%d/%d" % (today.month, today.day))
        if r.get("ok") and str(r.get("value") or "").strip():
            points = int(float(r["value"]))
    except Exception as e:
        print("WARN points:", e, file=sys.stderr)

    return {"rows": rows, "tracked_min": tracked_min, "points": points,
            "date": today.isoformat()}


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def fill_gap(desc: str, start_hhmm: str, end_hhmm: str) -> dict:
    m = _AT.search(desc)
    code = m.group(1) if m else ""
    desc_clean = _AT.sub("", desc).strip()
    pid = PROJECT_MAP.get(code)
    today = _dt.datetime.now(TZ).date()
    try:
        st = _dt.datetime.combine(today, _dt.time(*map(int, start_hhmm.split(":"))), TZ)
        en = _dt.datetime.combine(today, _dt.time(*map(int, end_hhmm.split(":"))), TZ)
    except Exception:
        return {"ok": False, "error": "bad time format (HH:MM)"}
    if en <= st:
        return {"ok": False, "error": "end must be after start"}
    dur = int((en - st).total_seconds())
    fmt = "%Y-%m-%dT%H:%M:%S%z"
    try:
        r = toggl_api.create_entry(desc_clean, st.strftime(fmt), en.strftime(fmt), dur,
                                   project_id=pid)
        return {"ok": bool(r), "project": code}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


_FMT = "%Y-%m-%dT%H:%M:%S%z"


def _get_entry(entry_id: str) -> dict | None:
    """True (un-clipped) entry from Toggl by id — the timeline's own rows are
    clipped to [midnight, now] for display (see _fetch_today), so any mutation
    must re-fetch the real record rather than trust a client-submitted HH:MM
    built from a clipped row (bug 2026-08-06: a 睡觉 entry starting 23:30
    yesterday displays as start=00:00 today; blindly resubmitting that would
    silently delete the real overnight minutes)."""
    today = _dt.datetime.now(TZ).date()
    entries = toggl_api.get_entries(
        start_date=(today - _dt.timedelta(days=1)).isoformat(),
        end_date=(today + _dt.timedelta(days=2)).isoformat()) or []
    for e in entries:
        if str(e.get("id")) == str(entry_id):
            return e
    return None


def _split_chunk_minutes(duration_min: float) -> int:
    """Fixed split-chunk size, scaled down for short entries so a 6-minute
    entry doesn't refuse to split just because 10 doesn't fit."""
    if duration_min < 5:
        return 1
    if duration_min < 10:
        return 5
    return 10


def _would_touch_logged(entry_id: str, start_dt: _dt.datetime, end_dt: _dt.datetime,
                        ledger: dict) -> bool:
    """True if retiming `entry_id` to [start_dt, end_dt) would make
    trim_range() delete/shrink another entry that's already been credited.
    trim_range replaces whatever it trims with brand-new ids (create_entry
    for the surviving remainder) that are absent from the ledger — silently
    re-swipeable/re-loggable minutes that were already counted once (bug
    2026-08-06, the same mechanism that tripled the quarterly-checkin task)."""
    day = start_dt.date()
    entries = toggl_api.get_entries(
        start_date=(day - _dt.timedelta(days=1)).isoformat(),
        end_date=(day + _dt.timedelta(days=2)).isoformat()) or []
    for e in entries:
        eid = str(e.get("id"))
        if eid == str(entry_id) or eid not in ledger:
            continue
        try:
            e_start = _parse_iso(e["start"])
        except Exception:
            continue
        running = (e.get("duration") or 0) < 0
        e_end = _dt.datetime.now(TZ) if running else (
            _parse_iso(e["stop"]) if e.get("stop") else None)
        if e_end is None or e_end <= start_dt or e_start >= end_dt:
            continue
        return True
    return False


def edit_entry(entry_id: str, desc: str, start_hhmm: str, end_hhmm: str,
               project_code: str) -> dict:
    """Edit description/project/time. Time changes go through the same
    trim_range() MECE-keeping path the desktop TUI uses. Guards (2026-08-06
    review): refuses to touch a cross-midnight entry's clipped time (can't
    safely reconstruct which calendar day a bare HH:MM belongs to), refuses
    to retime an already-logged entry, and refuses a retime that would bump
    trim_range into deleting/shrinking a DIFFERENT already-logged entry."""
    e = _get_entry(entry_id)
    if not e:
        return {"ok": False, "error": "entry not found (may have changed elsewhere)"}
    today = _dt.datetime.now(TZ).date()
    true_start = _parse_iso(e["start"])
    running = (e.get("duration") or 0) < 0 or not e.get("stop")
    true_end = None if running else _parse_iso(e["stop"])

    desc = (desc or "").strip()
    if not desc:
        return {"ok": False, "error": "description required"}
    fields: dict = {"description": desc}
    if project_code:
        pid = PROJECT_MAP.get(project_code)
        if pid is None:
            return {"ok": False, "error": f"unknown project @{project_code}"}
        fields["project_id"] = pid

    cur_start_hhmm = true_start.strftime("%H:%M")
    cur_end_hhmm = "now" if running else true_end.strftime("%H:%M")
    start_changed = bool(start_hhmm) and start_hhmm != cur_start_hhmm
    end_changed = bool(end_hhmm) and end_hhmm not in ("", "now") and end_hhmm != cur_end_hhmm

    if end_changed and running:
        return {"ok": False, "error": "still running — stop it first to set an end time"}

    if start_changed or end_changed:
        if true_start.date() != today or (true_end is not None and true_end.date() != today):
            return {"ok": False, "error": "cross-midnight entry — retime from desktop"}
        try:
            new_start = (_dt.datetime.combine(today, _dt.time(*map(int, start_hhmm.split(":"))), TZ)
                        if start_changed else true_start)
        except Exception:
            return {"ok": False, "error": "bad start time (HH:MM)"}

        if running:
            # No fixed end to reason about yet — just move the start.
            if new_start >= _dt.datetime.now(TZ):
                return {"ok": False, "error": "start must be in the past"}
            fields["start"] = new_start.strftime(_FMT)
        else:
            try:
                new_end = (_dt.datetime.combine(today, _dt.time(*map(int, end_hhmm.split(":"))), TZ)
                          if end_changed else true_end)
            except Exception:
                return {"ok": False, "error": "bad end time (HH:MM)"}
            if new_end <= new_start:
                return {"ok": False, "error": "end must be after start"}
            ledger = _ledger(today)
            if str(entry_id) in ledger:
                return {"ok": False, "error": "already logged — can't retime a logged entry"}
            if _would_touch_logged(entry_id, new_start, new_end, ledger):
                return {"ok": False, "error": "would overlap an already-logged entry — refusing"}
            try:
                toggl_api.trim_range(new_start, new_end, exclude_ids={e.get("id")})
            except Exception as ex:
                return {"ok": False, "error": f"trim failed (entry not yet moved): {ex}"[:200]}
            fields["start"] = new_start.strftime(_FMT)
            fields["stop"] = new_end.strftime(_FMT)
            fields["duration"] = int((new_end - new_start).total_seconds())

    try:
        toggl_api.update_entry(e.get("id"), **fields)
        return {"ok": True}
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:200]}


def split_entry(entry_id: str, mode: str) -> dict:
    """Split into a fixed chunk (see _split_chunk_minutes) + remainder.
    `mode="top"` carves the chunk off the START; `mode="bottom"` carves it
    off the END. Id-ownership always follows the EARLIER piece (matches the
    desktop TUI's ^P split convention) — the original id shrinks to become
    whichever piece comes first chronologically, and a new entry is created
    for whichever piece comes second. The new (later) piece is created
    BEFORE the original is shrunk (2026-08-06 review): if the shrink call
    then fails, the worst case is a transient overlap, not a permanently
    lost chunk of time."""
    if mode not in ("top", "bottom"):
        return {"ok": False, "error": "bad mode"}
    e = _get_entry(entry_id)
    if not e:
        return {"ok": False, "error": "entry not found (may have changed elsewhere)"}
    running = (e.get("duration") or 0) < 0 or not e.get("stop")
    if running:
        return {"ok": False, "error": "still running — stop it first"}
    today = _dt.datetime.now(TZ).date()
    start = _parse_iso(e["start"])
    end = _parse_iso(e["stop"])
    if start.date() != today or end.date() != today:
        return {"ok": False, "error": "cross-midnight entry — split from desktop"}

    duration_min = (end - start).total_seconds() / 60
    chunk = _split_chunk_minutes(duration_min)
    if duration_min <= chunk:
        return {"ok": False, "error": f"too short to split (need > {chunk}m)"}

    if str(entry_id) in _ledger(today):
        return {"ok": False, "error": "already logged — can't split a logged entry"}

    cut = (start + _dt.timedelta(minutes=chunk) if mode == "top"
          else end - _dt.timedelta(minutes=chunk))
    desc = e.get("description") or ""
    proj_id = e.get("project_id")
    tags = e.get("tags") or None

    try:
        toggl_api.create_entry(desc, cut.strftime(_FMT), end.strftime(_FMT),
                               int((end - cut).total_seconds()), proj_id, tags)
        toggl_api.update_entry(e.get("id"), stop=cut.strftime(_FMT),
                               duration=int((cut - start).total_seconds()))
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:200]}
    return {"ok": True, "chunk_minutes": chunk}


_DF_MOD = None  # lazy did-fast module (for habit-name lookups only)


def habit_tags(tags: list[str]) -> list[str]:
    """The subset of a Toggl entry's tags that name known habits (0n or 1n+
    headers/aliases) — those get a secondary minutes log on swipe (user
    request 2026-07-27: a run tagged 其他人 should credit both ledgers).
    Meta tags (-1/-2/-3/2, project codes, …) resolve to nothing and are
    ignored. Best-effort: an import failure just skips secondaries."""
    global _DF_MOD
    if not tags:
        return []
    try:
        if _DF_MOD is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("df_tags", DID_FAST)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["df_tags"] = mod
            spec.loader.exec_module(mod)
            _DF_MOD = mod
        df = _DF_MOD
        h = df.load_headers()
        known = {df.header_normalize(k)
                 for k in list(h.get("0n", {})) + list(h.get("1n", {}))}
        known |= {df.header_normalize(a) for a in df.ONENEON_ALIASES}
        return [t for t in tags if df.header_normalize(str(t)) in known]
    except Exception as e:
        print("WARN habit_tags:", e, file=sys.stderr)
        return []


def _run_did_fast(text: str) -> dict:
    """Shell did-fast.py and parse its JSON-in-stdout result. Shared by
    log_entry, convert_event (past-event branch) and done_current — all
    three just build a different did-fast command string."""
    try:
        proc = subprocess.run(["/usr/bin/python3", str(DID_FAST), text],
                              capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "did-fast timeout"}
    out = proc.stdout.strip()
    data = None
    brace = out.find("{")
    if brace >= 0:
        try:
            data = json.loads(out[brace:])
        except Exception:
            data = None
    step = None
    tag_steps = []
    if data and data.get("results"):
        step = data["results"][0].get("step")
        tag_steps = [f"{r.get('name')}→{r.get('step')}"
                     for r in data["results"][1:]]
    needs_agent = bool(data and data.get("agent_needed"))
    ok = proc.returncode == 0 and step is not None
    return {"ok": ok, "step": step, "tag_steps": tag_steps,
            "needs_agent": needs_agent,
            "stderr_tail": proc.stderr.strip()[-200:]}


def log_entry(entry_id: str, desc: str, minutes: int, project: str,
              tags: list[str] | None = None) -> dict:
    today = _dt.datetime.now(TZ).date()
    if str(entry_id) in _ledger(today):
        return {"ok": True, "already": True}
    # A dtd-started task carries its OWN point value (e.g. [30]), which is
    # generally NOT the same number as the entry's elapsed minutes. Passing
    # a bare number here only ever means anything to did-fast's Step 6
    # (variable, points=minutes) — a real Todoist-task match (Step 5)
    # ignores it entirely and re-derives points from whatever task its own
    # fuzzy word-overlap search happens to land on. Resolving it explicitly
    # here (exact match against the same task-queue cache dtd populates,
    # strictly more reliable than re-deriving via fuzzy search after the
    # fact) and passing it as an explicit [N] makes crediting deterministic
    # instead of dependent on did-fast re-finding the identical task
    # (2026-08-15 user report: dtd-started points weren't reliably landing).
    pts = _resolvable_points(desc)
    text = f"{desc} [{pts}]" if pts is not None else f"{desc} {minutes}"
    if project:
        text += f" @{project}"
    # Habit tags ride along as extra comma-separated /did items — did-fast
    # processes each independently (其他人 61 → 其他人 0n column, etc.).
    # These are always minutes (0n habit columns track time, not the
    # matched task's points), regardless of the main item's resolution.
    extra = habit_tags(tags or [])
    for t in extra:
        text += f", {t} {minutes}"
    r = _run_did_fast(text)
    if r["ok"]:
        note = f"{desc} {pts if pts is not None else minutes} → {r['step']}"
        if r["tag_steps"]:
            note += " + " + ", ".join(r["tag_steps"])
        _ledger_add(today, entry_id, note)
    return r


# In-flight guard for the two actions below: did-fast's point-writing is an
# ACCUMULATING formula append (not idempotent like log_entry's pre-check
# against the ledger, since there's no natural id to ledger a not-yet-
# existing Toggl entry against before the call completes) — a double-tap
# or retry landing while the first request is still in flight would credit
# points twice. Keyed per action+identity; removed in a finally so a failed
# call doesn't wedge future attempts.
_INFLIGHT: set[str] = set()


def convert_event(title: str, start_iso: str, end_iso: str, code: str, is_past: bool) -> dict:
    """Convert a calendar event into Toggl — mirrors tools/tg/janus.py's
    _convert_selected_event. An ALREADY-ENDED event backfills as a
    completed Toggl entry AND grants its points in one did-fast call (its
    Step 5.5 Toggl-entry-creation is keyed off the HHMM-HHMM range). A
    still-open/future event just starts a live tg-fast timer instead —
    there's nothing to grant points for yet."""
    # Sanitize independently of _fetch_events_today's own _safe_title call —
    # this is a public POST endpoint, not guaranteed to only ever receive a
    # title this server already cleaned.
    title = _safe_title(title)
    key = f"event:{title}|{start_iso}|{end_iso}"
    if key in _INFLIGHT:
        return {"ok": False, "error": "already converting"}
    _INFLIGHT.add(key)
    try:
        start_dt = _parse_iso(start_iso)
        suffix = f" @{code}" if code else ""
        if is_past:
            end_dt = _parse_iso(end_iso)
            text = f"{title} {start_dt:%H%M}-{end_dt:%H%M}{suffix}"
            r = _run_did_fast(text)
            return {"ok": r["ok"], "mode": "logged", "step": r.get("step"),
                    "needs_agent": r.get("needs_agent"), "stderr_tail": r.get("stderr_tail")}
        now = _dt.datetime.now(TZ)
        text = (f"{start_dt:%H%M} {title}{suffix}" if start_dt <= now
               else f"{title}{suffix}")
        try:
            proc = subprocess.run(["/usr/bin/python3", str(TG_FAST), text],
                                  capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "tg-fast timeout"}
        return {"ok": proc.returncode == 0, "mode": "started",
                "stderr_tail": proc.stderr.strip()[-200:]}
    finally:
        _INFLIGHT.discard(key)


def done_current(entry_id: str, desc: str, project: str) -> dict:
    """The /done action for the running timer: stop it AND grant its points
    in one shot — mirrors tools/tg/janus.py's _run_current_timer_done. A
    completed HHMM-nowHHMM did-fast command's Toggl-creation step calls
    trim_range() under the hood, which finds the still-open running entry
    covering that exact window and closes/trims it — no separate stop call
    needed. (Desktop's d357-recording-finalize branch and its interactive
    "no resolvable points, ask the user" prompt are both deliberately not
    ported here — out of scope per 2026-08-14 request; an unresolvable
    entry just logs 0分 same as a regular swiped entry already can. When
    the running entry DOES match a dtd task (_resolvable_points, see
    log_entry for why this is more reliable than did-fast's own re-match),
    its [N] rides the command explicitly — 2026-08-15 fix, same reasoning
    as log_entry."""
    key = f"done:{entry_id}"
    if key in _INFLIGHT:
        return {"ok": False, "error": "already logging"}
    _INFLIGHT.add(key)
    try:
        e = _get_entry(entry_id)
        if not e:
            return {"ok": False, "error": "entry not found (may have changed elsewhere)"}
        running = (e.get("duration") or 0) < 0 or not e.get("stop")
        if not running:
            return {"ok": False, "error": "not running anymore — reload"}
        try:
            start = _parse_iso(e["start"])
        except Exception:
            return {"ok": False, "error": "bad start time"}
        now = _dt.datetime.now(TZ)
        if (now - start).total_seconds() < 60:
            return {"ok": False, "error": "just started — give it a minute"}
        pts = _resolvable_points(desc)
        text = f"{desc} {start:%H%M}-{now:%H%M}"
        if pts is not None:
            text += f" [{pts}]"
        if project:
            text += f" @{project}"
        r = _run_did_fast(text)
        if r["ok"]:
            _ledger_add(_dt.datetime.now(TZ).date(), entry_id,
                       f"{desc} done {start:%H%M}-{now:%H%M} → {r['step']}")
        return r
    finally:
        _INFLIGHT.discard(key)


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/api/timeline")
def api_timeline():
    try:
        return jsonify({"ok": True, **build_timeline()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/fill", methods=["POST"])
def api_fill():
    b = request.get_json(force=True, silent=True) or {}
    desc = (b.get("desc") or "").strip()
    if not desc:
        return jsonify({"ok": False, "error": "no description"}), 400
    return jsonify(fill_gap(desc, b.get("start") or "", b.get("end") or ""))


@app.route("/api/log", methods=["POST"])
def api_log():
    b = request.get_json(force=True, silent=True) or {}
    if not b.get("id") or not b.get("desc"):
        return jsonify({"ok": False, "error": "id+desc required"}), 400
    return jsonify(log_entry(str(b["id"]), b["desc"].strip(),
                             int(b.get("minutes") or 0), (b.get("project") or "").strip(),
                             tags=b.get("tags") or []))


@app.route("/api/edit", methods=["POST"])
def api_edit():
    b = request.get_json(force=True, silent=True) or {}
    if not b.get("id"):
        return jsonify({"ok": False, "error": "id required"}), 400
    return jsonify(edit_entry(str(b["id"]), b.get("desc") or "",
                              (b.get("start") or "").strip(), (b.get("end") or "").strip(),
                              (b.get("project") or "").strip()))


@app.route("/api/split", methods=["POST"])
def api_split():
    b = request.get_json(force=True, silent=True) or {}
    if not b.get("id") or b.get("mode") not in ("top", "bottom"):
        return jsonify({"ok": False, "error": "id+mode(top|bottom) required"}), 400
    return jsonify(split_entry(str(b["id"]), b["mode"]))


@app.route("/api/convert-event", methods=["POST"])
def api_convert_event():
    b = request.get_json(force=True, silent=True) or {}
    if not b.get("title") or not b.get("start_iso") or not b.get("end_iso"):
        return jsonify({"ok": False, "error": "title+start_iso+end_iso required"}), 400
    return jsonify(convert_event(b["title"], b["start_iso"], b["end_iso"],
                                 (b.get("code") or "").strip(), bool(b.get("is_past"))))


@app.route("/api/done-current", methods=["POST"])
def api_done_current():
    b = request.get_json(force=True, silent=True) or {}
    if not b.get("id") or not b.get("desc"):
        return jsonify({"ok": False, "error": "id+desc required"}), 400
    return jsonify(done_current(str(b["id"]), b["desc"].strip(), (b.get("project") or "").strip()))


@app.route("/api/projects")
def api_projects():
    return jsonify({"ok": True, "codes": sorted(PROJECT_MAP.keys())})


@app.route("/")
def index():
    return render_template_string(PAGE)


# ---------------------------------------------------------------------------
# Frontend — same terminal styling as mobile dtd, single file, no deps
# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>janus</title>
<style>
  :root { --bg:#1b1b1b; --dim:#777; --go:#00e676; --gap:#555; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; height:100%; background:var(--bg); color:#cfcfcf;
    font:15px/1.2 ui-monospace,"SF Mono",Menlo,Monaco,"Cascadia Mono",monospace;
    -webkit-font-smoothing:antialiased; }
  header { position:sticky; top:0; z-index:5;
    padding:calc(env(safe-area-inset-top) + 9px) 14px 8px;
    background:#1b1b1bee; backdrop-filter:blur(6px);
    display:flex; align-items:center; justify-content:space-between;
    border-bottom:1px solid #2a2a2a; }
  header .brand { font-weight:700; letter-spacing:1px; }
  header .brand b { color:var(--go); }
  .tally { color:var(--dim); font-variant-numeric:tabular-nums; }
  .tally b { color:var(--go); }
  #reload { background:none; border:1px solid #333; color:var(--dim);
    border-radius:6px; padding:3px 9px; font-family:inherit; font-size:15px; }
  main { padding:2px 0 calc(env(safe-area-inset-bottom) + 60px); }
  .div { color:var(--dim); padding:8px 14px 2px; font-size:12px; letter-spacing:1px;
    border-top:1px solid #242424; }
  .row { position:relative; overflow:hidden; }
  .row .track { position:absolute; inset:0; background:var(--go); color:#003;
    font-weight:800; display:flex; align-items:center; padding-left:16px; opacity:0; }
  .row .track.edit { background:#2979ff; color:#001a3d;
    justify-content:flex-end; padding-left:0; padding-right:16px; }
  .line { position:relative; display:flex; align-items:center; gap:10px;
    padding:9px 14px; background:var(--bg); min-height:38px;
    transform:translateX(0); transition:transform .05s linear; will-change:transform;
    touch-action:pan-y; }
  .line.snap { transition:transform .22s cubic-bezier(.2,.7,.2,1); }
  .ttl { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .meta { white-space:nowrap; font-variant-numeric:tabular-nums; color:var(--dim); }
  .gaprow .ttl { color:var(--gap); font-style:italic; }
  .logged .ttl::after { content:" ✓"; color:var(--go); }
  .logged { opacity:.45; }
  .running .ttl::before { content:"▶ "; color:var(--go); }
  .eventrow { border-left:2px dashed currentColor; padding-left:12px; }
  .eventrow .ttl::before { content:"◇ "; }
  .converting { opacity:.45; pointer-events:none; }
  .empty,.loading { text-align:center; color:var(--dim); padding:60px 20px; }
  .toast { position:fixed; left:50%; bottom:calc(env(safe-area-inset-bottom) + 20px);
    transform:translateX(-50%) translateY(16px); background:var(--go); color:#003;
    font-weight:700; padding:9px 18px; border-radius:8px; opacity:0; transition:.22s;
    z-index:20; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
  .toast.err { background:#ff4081; color:#2a0010; }
  #dlg, #editDlg { position:fixed; inset:0; background:#000a; z-index:10; display:none;
    align-items:flex-end; }
  #dlg.show, #editDlg.show { display:flex; }
  #dlg .card, #editDlg .card { background:#232323; width:100%; padding:16px 16px
    calc(env(safe-area-inset-bottom) + 16px); border-radius:14px 14px 0 0; }
  #dlg h3, #editDlg h3 { margin:0 0 12px; font-size:15px; color:#cfcfcf; font-weight:700; }
  #dlg input, #editDlg input, #editDlg select { width:100%; background:#1b1b1b; border:1px solid #333;
    color:#cfcfcf; font:15px ui-monospace,Menlo,monospace; border-radius:8px; padding:10px 12px;
    margin-bottom:10px; }
  #dlg .times, #editDlg .times { display:flex; gap:10px; }
  #dlg .times input, #editDlg .times input { flex:1; text-align:center; }
  #dlg .btns, #editDlg .btns { display:flex; gap:10px; margin-top:4px; }
  #dlg button, #editDlg button { flex:1; font:700 15px ui-monospace,Menlo,monospace; border:none;
    border-radius:8px; padding:12px; }
  #dlg .save, #editDlg .save { background:var(--go); color:#003; }
  #dlg .cancel, #editDlg .cancel { background:#333; color:#aaa; }
  #editDlg .split { background:#2979ff; color:#001a3d; }
  #editDlg input:disabled { opacity:.4; }
</style>
</head>
<body>
<header>
  <div class="brand">jan<b>u</b>s</div>
  <div class="tally"><b id="pts">–</b> 分 · <span id="trk">0:00</span></div>
  <button id="reload">↻</button>
</header>
<main id="list"><div class="loading">loading…</div></main>

<div id="dlg">
  <div class="card">
    <h3>fill gap</h3>
    <input id="d-desc" placeholder="description (@code for project)" autocomplete="off">
    <div class="times">
      <input id="d-start" inputmode="numeric" placeholder="HH:MM">
      <input id="d-end" inputmode="numeric" placeholder="HH:MM">
    </div>
    <div class="btns">
      <button class="cancel" onclick="closeDlg()">cancel</button>
      <button class="save" onclick="saveDlg()">save</button>
    </div>
  </div>
</div>

<div id="editDlg">
  <div class="card">
    <h3>edit entry</h3>
    <input id="e-desc" placeholder="description" autocomplete="off">
    <div class="times">
      <input id="e-start" inputmode="numeric" placeholder="HH:MM">
      <input id="e-end" inputmode="numeric" placeholder="HH:MM">
    </div>
    <select id="e-project"></select>
    <div class="btns">
      <button class="cancel" onclick="closeEditDlg()">cancel</button>
      <button class="save" onclick="saveEdit()">save</button>
    </div>
    <div class="btns" style="margin-top:8px">
      <button class="split" onclick="doSplit('top')">split top</button>
      <button class="split" onclick="doSplit('bottom')">split bottom</button>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const list = document.getElementById('list');
const toastEl = document.getElementById('toast');
const dlg = document.getElementById('dlg');

function toast(msg, err){
  toastEl.textContent = msg;
  toastEl.classList.toggle('err', !!err);
  toastEl.classList.add('show');
  clearTimeout(toastEl._t);
  toastEl._t = setTimeout(()=>toastEl.classList.remove('show'), 1800);
}

async function load(){
  list.innerHTML = '<div class="loading">loading…</div>';
  try {
    const r = await fetch('/api/timeline');
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'fetch failed');
    document.getElementById('pts').textContent = (d.points==null?'–':d.points);
    document.getElementById('trk').textContent =
      Math.floor(d.tracked_min/60)+':'+String(d.tracked_min%60).padStart(2,'0');
    render(d.rows);
  } catch(e){ list.innerHTML = '<div class="empty">⚠ '+e.message+'</div>'; }
}

function render(rows){
  if(!rows.length){ list.innerHTML = '<div class="empty">no entries yet</div>'; return; }
  list.innerHTML = '';
  for(const r of rows){
    if(r.type === 'divider'){
      const d = document.createElement('div');
      d.className = 'div'; d.textContent = r.label;
      list.appendChild(d);
    } else {
      list.appendChild(makeRow(r));
    }
  }
  list.scrollIntoView(false);
  window.scrollTo(0, document.body.scrollHeight);
}

function trackLabel(r){
  if(r.type === 'gap') return '+ fill';
  if(r.type === 'event') return r.is_past ? 'log 分 ('+r.minutes+'m)' : (r.started ? 'resume tracking' : 'start tracking');
  // r.points is set when this entry's description exact-matches a real
  // dtd/Todoist task's [N] — show what will ACTUALLY be credited, not the
  // elapsed minutes (2026-08-15: those two numbers are frequently
  // different, and the swipe used to always imply minutes = points).
  if(r.running) return r.points != null ? 'done ✓ ('+r.points+'分)' : 'done ✓ (stop + log)';
  return r.points != null ? 'log '+r.points+'分' : 'neon log 分 ('+r.minutes+'m)';
}

function makeRow(r){
  const row = document.createElement('div');
  row.className = 'row';
  const track = document.createElement('div');
  track.className = 'track';
  track.textContent = trackLabel(r);
  row.appendChild(track);

  // Left-swipe reveal (edit) — entries only; gaps and calendar events have
  // nothing to edit (an event isn't a Toggl entry yet).
  let trackEdit = null;
  if(r.type === 'entry'){
    trackEdit = document.createElement('div');
    trackEdit.className = 'track edit';
    trackEdit.textContent = 'edit';
    row.appendChild(trackEdit);
  }

  const line = document.createElement('div');
  line.className = 'line' + (r.type==='gap'?' gaprow':'') + (r.type==='event'?' eventrow':'') +
    (r.logged?' logged':'') + (r.running?' running':'');
  if(r.type==='entry' || r.type==='event') line.style.color = r.color;
  const ttl = document.createElement('span');
  ttl.className = 'ttl';
  ttl.textContent = r.type==='gap' ? '· empty ·' : (r.type==='event' ? r.title : r.desc);
  const meta = document.createElement('span');
  meta.className = 'meta';
  meta.textContent = r.start+'–'+r.end+' · '+r.minutes+'m';
  line.appendChild(ttl); line.appendChild(meta);
  row.appendChild(line);
  bindSwipe(row, line, track, trackEdit, r);
  return row;
}

function bindSwipe(row, line, track, trackEdit, r){
  let x0=null, dx=0, dragging=false;
  const W = () => row.offsetWidth;
  const start = x=>{ x0=x; dx=0; dragging=true; line.classList.remove('snap'); };
  const move = x=>{
    if(!dragging) return;
    dx = x - x0;
    if(!trackEdit) dx = Math.max(0, dx);  // gap rows: right-swipe only
    line.style.transform = 'translateX('+dx+'px)';
    if(dx >= 0){
      track.style.opacity = Math.min(1, dx/(W()*0.4));
      if(trackEdit) trackEdit.style.opacity = 0;
    } else {
      track.style.opacity = 0;
      trackEdit.style.opacity = Math.min(1, -dx/(W()*0.4));
    }
  };
  const end = ()=>{
    if(!dragging) return; dragging=false;
    line.classList.add('snap');
    line.style.transform='translateX(0)';
    track.style.opacity=0; if(trackEdit) trackEdit.style.opacity=0;
    if(dx > W()*0.42) act(row, line, r);
    else if(trackEdit && dx < -W()*0.42) openEdit(r);
  };
  line.addEventListener('touchstart', e=>start(e.touches[0].clientX), {passive:true});
  line.addEventListener('touchmove',  e=>move(e.touches[0].clientX),  {passive:true});
  line.addEventListener('touchend', end);
  line.addEventListener('mousedown', e=>{start(e.clientX);
    const mm=ev=>move(ev.clientX), mu=()=>{end();
      document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);};
    document.addEventListener('mousemove',mm); document.addEventListener('mouseup',mu);});
}

let gapCtx = null;
function act(row, line, r){
  if(r.type === 'gap'){
    gapCtx = r;
    document.getElementById('d-desc').value = '';
    document.getElementById('d-start').value = r.start;
    document.getElementById('d-end').value = r.end;
    dlg.classList.add('show');
    setTimeout(()=>document.getElementById('d-desc').focus(), 60);
    return;
  }
  if(r.type === 'event'){ commitConvert(line, r); return; }
  if(r.running){ commitDone(line, r); return; }
  if(r.logged){ toast('already logged', true); return; }
  commitLog(line, r);
}

async function commitConvert(line, r){
  line.classList.add('converting');  // lock immediately — did-fast's point
                                      // write isn't idempotent like commitLog's ledger pre-check
  try {
    const resp = await fetch('/api/convert-event', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({title:r.title, start_iso:r.start_iso, end_iso:r.end_iso,
        code:r.project, is_past:r.is_past})});
    const d = await resp.json();
    if(!d.ok){
      line.classList.remove('converting');
      toast(d.needs_agent ? 'no route — use /did on desktop' : (d.error||'convert failed'), true);
      return;
    }
    toast(d.mode === 'logged' ? ('+'+r.minutes+'m → '+(d.step||'neon')+' ✓') : 'tracking started ✓');
    load();  // the event's slot is now covered by a real Toggl entry
  } catch(e){ line.classList.remove('converting'); toast('offline', true); }
}

async function commitDone(line, r){
  line.classList.add('converting');
  try {
    const resp = await fetch('/api/done-current', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:r.id, desc:r.desc, project:r.project})});
    const d = await resp.json();
    if(!d.ok){
      line.classList.remove('converting');
      toast(d.needs_agent ? 'no route — use /did on desktop' : (d.error||'done failed'), true);
      return;
    }
    toast('stopped + logged → '+(d.step||'neon')+' ✓');
    load();  // the running row is now a completed, logged entry
  } catch(e){ line.classList.remove('converting'); toast('offline', true); }
}

async function commitLog(line, r){
  line.classList.add('logged');
  try {
    const resp = await fetch('/api/log', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:r.id, desc:r.desc, minutes:r.minutes, project:r.project, tags:r.tags||[]})});
    const d = await resp.json();
    if(d.already){ toast('already logged'); return; }
    if(!d.ok){
      line.classList.remove('logged');
      toast(d.needs_agent ? 'no route — use /did on desktop' : 'log failed', true);
      return;
    }
    let msg = '+'+r.minutes+'m → '+(d.step||'neon');
    if(d.tag_steps && d.tag_steps.length) msg += ' + '+d.tag_steps.join(', ');
    toast(msg+' ✓');
  } catch(e){ line.classList.remove('logged'); toast('offline', true); }
}

function closeDlg(){ dlg.classList.remove('show'); gapCtx=null; }
async function saveDlg(){
  const desc = document.getElementById('d-desc').value.trim();
  const start = document.getElementById('d-start').value.trim();
  const end = document.getElementById('d-end').value.trim();
  if(!desc){ toast('need a description', true); return; }
  closeDlg();
  try {
    const r = await fetch('/api/fill', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({desc, start, end})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'create failed', true); return; }
    toast('tracked ✓' + (d.project?' → '+d.project:''));
    load();
  } catch(e){ toast('offline', true); }
}

let projectCodes = [];
async function loadProjects(){
  try {
    const r = await fetch('/api/projects');
    const d = await r.json();
    if(d.ok) projectCodes = d.codes;
  } catch(e){ /* dropdown just stays empty — non-fatal */ }
}

const editDlg = document.getElementById('editDlg');
let editCtx = null;

function openEdit(r){
  editCtx = r;
  document.getElementById('e-desc').value = r.desc;
  document.getElementById('e-start').value = r.start;
  const endEl = document.getElementById('e-end');
  endEl.value = r.end;
  endEl.disabled = !!r.running;   // no fixed end yet — stop it first (desktop mirrors this)
  const sel = document.getElementById('e-project');
  sel.innerHTML = '<option value="">(none)</option>' +
    projectCodes.map(c=>'<option value="'+c+'"'+(c===r.project?' selected':'')+'>'+c+'</option>').join('');
  editDlg.classList.add('show');
}

function closeEditDlg(){ editDlg.classList.remove('show'); editCtx = null; }

async function saveEdit(){
  if(!editCtx) return;
  const desc = document.getElementById('e-desc').value.trim();
  const start = document.getElementById('e-start').value.trim();
  const endEl = document.getElementById('e-end');
  const end = endEl.disabled ? '' : endEl.value.trim();
  const project = document.getElementById('e-project').value;
  if(!desc){ toast('need a description', true); return; }
  const id = editCtx.id;
  closeEditDlg();
  try {
    const r = await fetch('/api/edit', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id, desc, start, end, project})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'edit failed', true); return; }
    toast('saved ✓');
    load();
  } catch(e){ toast('offline', true); }
}

async function doSplit(mode){
  if(!editCtx) return;
  const id = editCtx.id;
  closeEditDlg();
  try {
    const r = await fetch('/api/split', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id, mode})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'split failed', true); return; }
    toast('split ✓ ('+d.chunk_minutes+'m)');
    load();
  } catch(e){ toast('offline', true); }
}

document.getElementById('reload').onclick = load;
loadProjects();
load();
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
