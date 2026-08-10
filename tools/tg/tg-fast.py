#!/usr/bin/env python3
"""tg-fast.py — Fast /tg dispatcher. Resolves shortcodes and calls toggl_cli.

Usage:
    python3 tg-fast.py "stats"           # start timer
    python3 tg-fast.py "stop"            # stop timer
    python3 tg-fast.py "today"           # show today
    python3 tg-fast.py "current"         # show current
    python3 tg-fast.py "del 12345"       # delete entry
    python3 tg-fast.py "work 9-10"       # create completed entry
    python3 tg-fast.py "1823 o314"       # backdated start
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
# Set by main() from a `--date YYYY-MM-DD` arg (janus viewing a past day);
# only ever a NON-today date. Range creates honor it; live-timer forms error.
_DATE_OVERRIDE: date | None = None
# Entry ids created earlier in THIS invocation. A multi-item command with
# overlapping ranges ("1427-1447 冥想 #xk26, 1427-1652 hcm #-1", 2026-07-31)
# had item 2's MECE trim silently DELETE the entry item 1 just created —
# deliberate overlaps between the user's own batch items must survive.
_CREATED_IDS: set = set()
CLI = str(Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py")


def _toggl_api():
    """Load toggl_cli.py's own toggl_api handle (importlib — same trick
    did-fast.py uses) instead of duplicating its API-key loading and
    sys.path setup here. Lazy: only paid by callers that actually touch
    Toggl directly (the trim-overlap check), not the ordinary _run_cli
    subprocess path every other command already uses."""
    spec = importlib.util.spec_from_file_location("toggl_cli_lib", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.toggl_api
CACHE = str(Path.home() / ".claude/skills/tg/cache.json")
DO_SESSION = Path.home() / ".claude/skills/do/active.json"
DID_FAST = str(Path.home() / "i446-monorepo/tools/did/did-fast.py")
import sys as _sys; _sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib")); import state_paths as _sp
TASK_QUEUE = str(_sp.TASK_QUEUE)
JANUS_PID = Path.home() / ".cache" / "janus.pid"

# ── Shortcode table ──────────────────────────────────────────────────────────

SHORTCODES = {
    # High frequency
    "الفاتحة": ("hcm", []), "睡觉": ("睡觉", ["-3"]), "fall asleep": ("hcmc", ["-1"]),
    "0t": ("n156", []), "新闻": ("hcmc", ["-3"]), "work": ("i9", []),
    "family time": ("xk87", []), "read": ("xk87", ["-3"]), "0l": ("g245", []),
    "math": ("xk87", []), "冥想": ("hcm", []), "day hci": ("hci", []),
    "wake up": ("infra", []), "bball": ("hcbp", []), "其他人": ("hcm", []),
    "-1l": ("g245", []), "o314": ("hcm", []), "hiit": ("hcbp", ["-2"]),
    "vibing": ("i9", []), "hcmr": ("hcm", []), "kn47 daily": ("m5x2", []),
    "0g": ("g245", []), "epcn": ("epcn", []), "h lunch": ("hcb", []),
    "meetings": ("i9", []), "tasks": ("i9", []), "ren to sleep": ("xk87", []),
    "1s": ("g245", []), "get up": ("infra", []), "词汇": ("hcmc", ["-3"]),
    "doze": ("hcmc", ["-1"]), "youtube": ("hcmc2", ["2"]), "stats": ("i9", []),
    "out the door": ("infra", []),
    # Medium frequency
    "h breakfast": ("hcb", []), "breakfast": ("hcb", []), "早餐": ("hcb", []), "dinner": ("xk87", []),
    "lunch": ("hcb", []), "h dinner": ("hcb", []), "dad call": ("家", []),
    "lx walk": ("xk88", []), "r203 weekly": ("m5x2", []), "r202 weekly": ("m5x2", []),
    "kids to sleep": ("xk87", []), "lego": ("xk87", []), "notes": ("i9", []),
    "业写": ("i9", []),
    "-1t": ("n156", []), "starcraft": ("hcmc2", ["2"]),
    "im|jm 1|1": ("m5x2", []), "الشمس": ("hcm", []), "news": ("hcmc", ["-3"]),
    "teams": ("i9", []), "m5x2 people": ("m5x2", []),
    "m5x2 strat (1|1|1)": ("m5x2", []), "return home": ("xk87", []),
    "bio": ("infra", []), "lx chat": ("xk88", []), "lx call": ("xk88", []),
    "mom call": ("s897", []), "1 hcme": ("hcm", []), "day": ("hci", []),
    "snack": ("hcb", []), "m5x2 accounting & analytics": ("m5x2", []),
    "slt": ("i9", []), "exp meeting": ("i9", []),
    "w225 + l912 weekly": ("m5x2", []), "coffee": ("epcn", []),
    "stuart call": ("s897", []), "family breakfast": ("xk87", []),
    "family dinner": ("xk87", []), "weekly update": ("i9", []),
    "f693": ("i9", []), "shower": ("hci", []), "slt prep": ("i9", []),
    "-1g": ("g245", []), "النور": ("hcm", []), "pack": ("i444", []),
    "1 xk87": ("xk87", []), "1 -1n": ("g245", []), "ana 1|1": ("i9", []),
    "1 -2g": ("g245", []), "to uber": ("i444", []), "lx checkin": ("xk88", []),
    "metrics meeting": ("i9", []), "carolina 1|1": ("i9", []),
    "fix computer": ("i9", []), "through airport": ("i444", []),
    "ibx": ("m5x2", []), "plan weekend": ("xk87", []),
    "hospital time": ("xk87", []), "generic placeholder": ("infra", []),
    "unsure": ("infra", []),
    "stats m5x2": ("m5x2", []),
    # Kid-time (Theo/Ren/Rori) → family project
    "xk20": ("xk87", []), "xk22": ("xk87", []), "xk26": ("xk87", []),
}

# Domain-only shortcodes
DOMAINS = {
    "hcm", "hcmc", "hcb", "hcbp", "hci", "i9", "m5x2", "xk87", "xk88",
    "s897", "epcn", "g245", "n156", "i444", "infra", "家", "睡觉",
}

# Pattern: "1 <domain>" maps to that domain
_ONE_PREFIX = re.compile(r'^1\s+(\S+)$', re.IGNORECASE)

# Valid Toggl project codes (loaded once from config)
_TOGGL_PROJECTS = None

def _get_toggl_projects():
    global _TOGGL_PROJECTS
    if _TOGGL_PROJECTS is not None:
        return _TOGGL_PROJECTS
    try:
        cfg = Path(__file__).resolve().parent.parent.parent / "mcp/toggl_server/config.py"
        ns = {}
        exec(cfg.read_text(), ns)
        _TOGGL_PROJECTS = set(ns.get("PROJECT_MAP", {}).keys())
    except Exception:
        _TOGGL_PROJECTS = set()
    return _TOGGL_PROJECTS


_ANNOTATION_RE = re.compile(r' *\(\d*\)| *\[\d*\]| *\{\d*\}')


def _norm_time(t: str) -> str:
    """Normalize a loose time token to HH:MM. Accepts HH:MM (passthrough),
    HHMM (4-digit, e.g. '2200' -> '22:00'), or a bare 1-2 digit hour
    ('9' -> '09:00')."""
    if ":" in t:
        return t
    if len(t) == 4:
        return t[:2] + ":" + t[2:]
    if len(t) == 3:
        return "0" + t[0] + ":" + t[1:]
    return t.zfill(2) + ":00"


def _strip_annotations(s: str) -> str:
    return re.sub(r'  +', ' ', _ANNOTATION_RE.sub('', s)).strip()


def _search_task_cache(content: str, valid: set) -> str:
    """Search task-queue.json for content, return first label that's a valid Toggl project."""
    try:
        data = json.loads(Path(TASK_QUEUE).read_text())
    except Exception:
        return ""
    section_tags = {"0neon", "1neon", "夜neon", "关键路径", "#0g", "#-1g"}
    clean = _strip_annotations(content).lower()
    for section in data.values():
        if not isinstance(section, list):
            continue
        for task in section:
            if not isinstance(task, dict):
                continue
            task_clean = _strip_annotations(task.get("content", "")).lower()
            if task_clean == clean:
                for label in task.get("labels", []):
                    if label in valid and label not in section_tags:
                        return label
                return ""
    return ""


def _project_from_task_cache(content: str) -> str:
    """Look up task content in task-queue.json, return first label that's a valid Toggl project.
    On cache miss, refreshes the cache once and retries."""
    valid = _get_toggl_projects()
    if not valid:
        return ""
    result = _search_task_cache(content, valid)
    if result:
        return result
    # Cache miss: refresh and retry once
    try:
        subprocess.run(
            ["python3", DID_FAST, "--refresh-cache"],
            capture_output=True, timeout=30,
        )
    except Exception:
        return ""
    return _search_task_cache(content, valid)


def _run_cli(*args):
    r = subprocess.run(
        ["python3", CLI, *args],
        capture_output=True, text=True, timeout=10,
    )
    out = (r.stdout.strip() + "\n" + r.stderr.strip()).strip()
    return out


def _update_cache(running=None):
    try:
        cache = {}
        if Path(CACHE).exists():
            cache = json.loads(Path(CACHE).read_text())
        cache["running"] = running
        Path(CACHE).parent.mkdir(parents=True, exist_ok=True)
        Path(CACHE).write_text(json.dumps(cache))
    except Exception:
        pass


def resolve_do_session():
    """If a /do session is active, resolve it: stop timer, compute duration, run /did."""
    if not DO_SESSION.exists():
        return
    try:
        session = json.loads(DO_SESSION.read_text())
        task = session.get("task", "")
        started = session.get("started_at", "")
        if not task or not started:
            DO_SESSION.unlink(missing_ok=True)
            return

        duration_min = None

        # Check if the /do timer is still running
        cur = _run_cli("current")
        if "Running:" in cur and task.lower() in cur.lower():
            stop_out = _run_cli("stop")
            _update_cache(None)
            # Parse "Stopped: desc (42min)" or similar
            m = re.search(r'\((\d+)\s*min', stop_out)
            if m:
                duration_min = int(m.group(1))

        if duration_min is None:
            # Timer already stopped elsewhere; fall back to started_at → now
            started_dt = datetime.fromisoformat(started)
            duration_min = max(1, int((datetime.now() - started_dt).total_seconds() / 60))

        # Run did-fast.py with task + duration as points
        result = subprocess.run(
            ["python3", DID_FAST, f"{task} {duration_min}"],
            capture_output=True, text=True, timeout=30,
        )
        print(f"Resolved /do: {task} → {duration_min}min", file=sys.stderr)
        DO_SESSION.unlink(missing_ok=True)
    except Exception as e:
        print(f"WARN: /do session resolve failed: {e}", file=sys.stderr)


def _strip_tag_tokens(s: str) -> str:
    """Drop #tag and @project tokens from a description fallback. resolve()
    empties the desc for domain-only items ("hcm"), and the raw-text
    fallback at the range call sites was reinstating the tokens verbatim —
    the 2026-07-31 "hcm #-1" entry was literally named "hcm #-1", and
    "0725-0734 @i9" (2026-08-01) created an entry NAMED "@i9"."""
    return re.sub(r'\s*(?:#-?\w+|@\w+)', '', s).strip()


def resolve(raw: str):
    """Return (description, project, tags)."""
    desc = raw.strip()
    project = ""
    tags = []
    override = False

    # Extract #tag tokens (anywhere in the string) → Toggl tags
    tag_matches = re.findall(r'#(-?\w+)', desc)
    explicit_tags = bool(tag_matches)
    if tag_matches:
        tags = tag_matches
        desc = re.sub(r'\s*#-?\w+', '', desc).strip()

    # Extract @project override (anywhere in the string)
    m = re.search(r'@(\w+)', desc)
    if m:
        project = m.group(1)
        desc = re.sub(r'\s*' + re.escape(m.group(0)), '', desc, count=1).strip()
        override = True

    if not override:
        key = desc.lower()
        # Exact shortcode match
        if key in SHORTCODES:
            sc_project, sc_tags = SHORTCODES[key]
            project = sc_project
            if not explicit_tags:
                tags = sc_tags
        # Domain-only
        elif key in DOMAINS:
            project = key
            desc = ""
        # "1 <domain>" pattern
        else:
            pm = _ONE_PREFIX.match(desc)
            if pm and pm.group(1).lower() in DOMAINS:
                project = pm.group(1).lower()

    # Fallback: check task-queue.json labels for a valid Toggl project
    if not project and not override:
        project = _project_from_task_cache(raw)

    # hcmc entries whose description STARTS with "review" are review/critique
    # work, not consumption -- route them to hcmr instead (2026-07-31 user
    # request). Only when hcmc was inferred, never when the user explicitly
    # forced it with @hcmc -- an explicit override always wins as-is.
    desc_words = desc.split()
    if not override and project == "hcmc" and desc_words and desc_words[0].lower() == "review":
        project = "hcmr"

    return desc, project, tags


def cmd_start(desc, project, tags):
    # toggl_cli's start reads positionally: desc, [project], [tags...]. project
    # must occupy slot 1 whenever ANYTHING follows it (a real project code, or
    # tags with no project) — otherwise a tag like "-2" slides into the project
    # slot and toggl_cli's _resolve_project falls back to int("-2") = -2, a
    # bogus project id that 400s against the API (regression 2026-08-10: "/tg
    # prep bball #-2" — passthrough desc, no shortcode project, explicit tag).
    # Domain-only shortcodes (desc="") need the SAME slot-1 placement for
    # project, not project substituting for desc — the old `else` branch put
    # the domain code in the description slot instead of the project slot.
    args = ["start", desc, project] if (project or tags) else ["start", desc]
    if tags:
        args.extend(tags)
    out = _run_cli(*args)
    _update_cache({"desc": desc or project, "project": project})
    return out


def cmd_stop():
    out = _run_cli("stop")
    _update_cache(None)
    return out


def cmd_create_range(desc, project, tags, start_t, end_t):
    """Create a completed entry for an explicit "<desc> <start>-<end>" range.

    Trims/splits/deletes any existing entry that overlaps the new range
    first (user request 2026-07-19: editing/creating time entries must stay
    MECE — shorten an overlapping entry to make room, or delete it outright
    on full overlap). This mirrors did-fast.py's identical fix for /did
    time-range items (2026-07-16, the "asha"/"asha prep" double-count) —
    both now delegate to the same toggl_api.trim_range.

    A `--date YYYY-MM-DD` in the input (janus viewing a past day) retargets
    the whole thing — trim window and created entry — to that date."""
    today = _DATE_OVERRIDE or datetime.now(TZ).date()

    def _parse(t):
        h, m = int(t[:2]), int(t[3:5])
        return datetime(today.year, today.month, today.day, h, m, tzinfo=TZ)

    start_dt, end_dt = _parse(start_t), _parse(end_t)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    try:
        # exclude_ids: never trim an entry created by an EARLIER item of the
        # same invocation — overlapping batch items are deliberate.
        trim_lines = _toggl_api().trim_range(start_dt, end_dt,
                                             exclude_ids=_CREATED_IDS)
    except Exception as e:  # noqa: BLE001 — never block entry creation on a trim failure
        trim_lines = [f"trim failed: {e}"]

    args = ["create", desc, start_t, end_t]
    if project:
        args.append(project)
    for tag in tags:
        args.extend(["--tag", tag])
    if _DATE_OVERRIDE:
        args.extend(["--date", _DATE_OVERRIDE.isoformat()])
    out = _run_cli(*args)
    id_m = re.search(r"\[id:(\d+)\]", out or "")
    if id_m:
        _CREATED_IDS.add(int(id_m.group(1)))
    if trim_lines:
        out = "\n".join(trim_lines) + ("\n" + out if out else "")
    return out


def _trim_overlapping(back_min, results):
    """Find and trim any completed entries that overlap the backdate time."""
    today_out = _run_cli("today")
    # Parse entries: "HH:MM-HH:MM <desc> @<project> (Nm) [id:NNN]"
    for line in today_out.split("\n"):
        m = re.match(r'\s*(\d{2}:\d{2})-(\d{2}:\d{2})\s+(.+?)(?:\s+@(\S+))?\s+\(\d+', line)
        if not m:
            continue
        start_s, end_s, e_desc, e_proj = m.group(1), m.group(2), m.group(3).strip(), m.group(4) or ""
        s_min = int(start_s[:2]) * 60 + int(start_s[3:])
        e_min = int(end_s[:2]) * 60 + int(end_s[3:])
        # Entry overlaps if it starts before backdate and ends after backdate
        if s_min < back_min and e_min > back_min:
            id_match = re.search(r'\[id:(\d+)\]', line)
            if not id_match:
                continue
            entry_id = id_match.group(1)
            trim_end_min = back_min - 1
            trim_end = "%02d:%02d" % (trim_end_min // 60, trim_end_min % 60)
            _run_cli("delete", entry_id)
            create_args = ["create", e_desc, start_s, trim_end]
            if e_proj:
                create_args.append(e_proj)
            _run_cli(*create_args)
            results.append("Trimmed: %s %s-%s @%s" % (e_desc, start_s, trim_end, e_proj))


def cmd_backdated(backtime, desc, project, tags):
    """Stop current, trim overlapping entries, start backdated."""
    results = []
    hhmm = backtime[:2] + ":" + backtime[2:]
    back_h, back_m = int(backtime[:2]), int(backtime[2:])
    back_min = back_h * 60 + back_m

    # Handle running timer first
    cur = _run_cli("current")
    if "Running:" in cur:
        id_match = re.search(r'\[id:(\d+)\]', cur)
        time_match = re.search(r'(\d{2}:\d{2})-running', cur)
        desc_match = re.search(r'\d{2}:\d{2}-running\s+(.+?)(?:\s+@(\S+))?\s+\(', cur)

        old_id = id_match.group(1) if id_match else None
        old_start = time_match.group(1) if time_match else None
        old_desc = desc_match.group(1).strip() if desc_match else None
        old_proj = desc_match.group(2) if desc_match and desc_match.group(2) else None

        stop_out = _run_cli("stop")
        results.append(stop_out)

        # Trim the just-stopped entry
        if old_id and old_start:
            old_h, old_m = int(old_start.split(":")[0]), int(old_start.split(":")[1])
            old_min = old_h * 60 + old_m
            if old_min < back_min:
                trim_end = "%02d:%02d" % ((back_min - 1) // 60, (back_min - 1) % 60)
                _run_cli("delete", old_id)
                create_args = ["create", old_desc or "unknown", old_start, trim_end]
                if old_proj:
                    create_args.append(old_proj)
                _run_cli(*create_args)
                results.append("Trimmed: %s %s-%s @%s" % (old_desc, old_start, trim_end, old_proj or ""))

    # Also trim any completed entries that overlap the backdate time
    _trim_overlapping(back_min, results)

    # Start backdated
    args = ["start", desc or project]
    if project and desc:
        args.append(project)
    if tags:
        args.extend(tags)
    args.extend(["--at", hhmm])
    start_out = _run_cli(*args)
    results.append(start_out)
    _update_cache({"desc": desc or project, "project": project})
    return "\n".join(results)


def main():
    if len(sys.argv) < 2:
        print("Usage: tg-fast.py <args>")
        sys.exit(1)

    raw = " ".join(sys.argv[1:]).strip()

    # --date YYYY-MM-DD (janus viewing a past day): range creates land on
    # that date. Live-timer forms make no sense in the past and error below.
    global _DATE_OVERRIDE
    date_m = re.search(r"\s*--date\s+(\d{4}-\d{2}-\d{2})\b", raw)
    if date_m:
        try:
            d = date.fromisoformat(date_m.group(1))
        except ValueError:
            print(f"err: bad --date {date_m.group(1)}")
            sys.exit(1)
        raw = (raw[:date_m.start()] + raw[date_m.end():]).strip()
        if d != datetime.now(TZ).date():
            _DATE_OVERRIDE = d

    # Simple commands
    if raw.lower().startswith("--resolve "):
        _, project, _ = resolve(raw[10:])
        print(project)
        return
    if raw.lower() == "stop":
        print(cmd_stop())
        return
    if raw.lower() == "today":
        print(_run_cli("today"))
        return
    if raw.lower() == "current":
        print(_run_cli("current"))
        return
    if raw.lower().startswith("del "):
        entry_id = raw[4:].strip()
        print(_run_cli("delete", entry_id))
        return

    # Resolve orphaned /do session before starting any new timer
    resolve_do_session()

    # Multiple entries separated by , ; or their fullwidth CJK forms ，；  →
    # create each independently. Time ranges (9-10, 9:30-10:15) and backdates
    # never contain these, so the split is safe. A fullwidth comma from CJK
    # input used to leave the whole string as one bogus entry.
    entries = [e.strip() for e in re.split(r"[,;，；]", raw) if e.strip()]
    print("\n".join(_process_entry(e) for e in entries))


def _process_entry(raw: str) -> str:
    """Resolve and create/start a single timer entry; return the CLI output line."""
    # Peel off a trailing @project override BEFORE range detection. Every
    # range regex below anchors the range to the very start or very end of
    # the string, so "desc TIME-TIME @project" (the documented <desc>
    # <start>-<end> @<project> syntax) previously matched NEITHER range
    # pattern — @project sits after the range, breaking the trailing-range
    # anchor — and silently fell through to "start a timer with the whole
    # raw string as description". Since Toggl auto-stops the current running
    # entry on any new start, that corrupted an unrelated timer (2026-07-13).
    at_override = re.search(r'\s@(\S+)\s*$', raw)
    project_suffix = ""
    if at_override:
        project_suffix = " " + at_override.group(0).strip()
        raw = raw[:at_override.start()].rstrip()

    # Check for time range: "desc HH:MM-HH:MM" or "HH:MM-HH:MM desc" or "desc H-H"
    # Try range at end first, then at start
    range_match = re.search(r'(\d{1,4}(?::\d{2})?)\s*-\s*(\d{1,4}(?::\d{2})?)\s*$', raw)
    if not range_match:
        range_match_start = re.match(r'^(\d{1,4}(?::\d{2})?)\s*-\s*(\d{1,4}(?::\d{2})?)\s+(.+)$', raw)
        if range_match_start:
            s, e = range_match_start.group(1), range_match_start.group(2)
            # Validate as HHMM-HHMM (4-digit no colon) or HH:MM-HH:MM
            if ":" not in s and len(s) == 4:
                s = s[:2] + ":" + s[2:]
            if ":" not in e and len(e) == 4:
                e = e[:2] + ":" + e[2:]
            if ":" in s and ":" in e:
                desc_part = (range_match_start.group(3).strip() + project_suffix).strip()
                desc, project, tags = resolve(desc_part)
                return cmd_create_range(desc or _strip_tag_tokens(desc_part),
                                        project, tags, s, e)
    if range_match:
        start_t = _norm_time(range_match.group(1))
        end_t = _norm_time(range_match.group(2))
        desc_part = (raw[:range_match.start()].strip() + project_suffix).strip()
        desc, project, tags = resolve(desc_part)
        return cmd_create_range(desc or _strip_tag_tokens(desc_part),
                                project, tags, start_t, end_t)

    # No range matched — restore the @project suffix for the backdate/default
    # paths below, which already handle @ anywhere in the string via resolve().
    raw = raw + project_suffix

    # A live timer (plain start or HHMM backdate) can't run on a past day —
    # only completed ranges can target one.
    if _DATE_OVERRIDE:
        return (f"err: viewing {_DATE_OVERRIDE:%-m/%-d} — use '<desc> HHMM-HHMM' "
                "to log an entry on that day (start/stop act on today)")

    # Check for backdated start: "HHMM desc" or "desc HHMM"
    backdate_match = re.match(r'^(\d{4})\s+(.+)$', raw)
    if backdate_match:
        backtime = backdate_match.group(1)
        h, m = int(backtime[:2]), int(backtime[2:])
        if 0 <= h <= 23 and 0 <= m <= 59:
            rest = backdate_match.group(2)
            desc, project, tags = resolve(rest)
            return cmd_backdated(backtime, desc, project, tags)

    # Check for backdated start: "desc HHMM" (time at end)
    backdate_end_match = re.search(r'\s(\d{4})$', raw)
    if backdate_end_match:
        backtime = backdate_end_match.group(1)
        h, m = int(backtime[:2]), int(backtime[2:])
        if 0 <= h <= 23 and 0 <= m <= 59:
            rest = raw[:backdate_end_match.start()].strip()
            desc, project, tags = resolve(rest)
            return cmd_backdated(backtime, desc, project, tags)

    # Default: start timer
    desc, project, tags = resolve(raw)
    return cmd_start(desc, project, tags)


def notify_tui():
    """Signal janus to refresh immediately via SIGUSR1."""
    try:
        pid = int(JANUS_PID.read_text().strip())
        os.kill(pid, signal.SIGUSR1)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass


if __name__ == "__main__":
    main()
    notify_tui()
