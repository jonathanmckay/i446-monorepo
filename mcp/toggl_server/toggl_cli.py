#!/usr/bin/env python3
"""Standalone Toggl CLI — used by /tg skill to avoid MCP overhead.

Usage:
  toggl_cli.py start <description> [project] [tag1 tag2 ...]
  toggl_cli.py stop
  toggl_cli.py current
  toggl_cli.py today
  toggl_cli.py create <description> <HH:MM> <HH:MM> [project] [--date YYYY-MM-DD]
  toggl_cli.py delete <entry_id>
"""
import sys
import os
import json
import signal
import datetime

# Self-locating: guarantees `daytime` resolves regardless of the caller's
# own sys.path — see lib/daytime.py, the shared "now"/"today" resolution
# every DTD/Janus TZ read must go through (this file used to hardcode
# ZoneInfo("America/Los_Angeles") for the day-barrier midnight split, wrong
# the moment the device follows local time while traveling).
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib"))
import daytime  # noqa: E402

# Load API key from ~/.claude.json MCP config if not already set
def _load_api_key():
    try:
        claude_json = os.path.expanduser("~/.claude.json")
        with open(claude_json) as f:
            d = json.load(f)
        key = (d.get("mcpServers", {})
                 .get("toggl_server", {})
                 .get("env", {})
                 .get("TOGGL_API_KEY", ""))
        if key:
            return key
    except Exception:
        pass
    return ""

if not os.environ.get("TOGGL_API_KEY"):
    os.environ["TOGGL_API_KEY"] = _load_api_key()
os.environ.setdefault("TOGGL_WORKSPACE_ID", "2092616")

# Add parent dir so `from toggl_server.x import ...` works
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toggl_server.config import PROJECT_MAP, PROJECT_NAMES  # noqa: E402
from toggl_server import toggl_api  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

def _tz():
    """Live-resolved active timezone — see lib/daytime.py. Not cached: an
    in-progress /travel change or an OS TZ change must be picked up on the
    next call, not frozen at import. Governs the day-barrier midnight split
    in cmd_create and the 'today' window in cmd_today."""
    return daytime.active_zone()


def __getattr__(name):
    # PEP 562 module __getattr__: `toggl_cli.TZ` used to be a constant
    # frozen at import. Anything (tests included) that still reaches for
    # `.TZ` gets the same live-resolved zone as every internal call.
    if name == "TZ":
        return daytime.active_zone()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _resolve_project(code):
    if not code:
        return None
    c = code.lower().strip()
    if c in PROJECT_MAP:
        return PROJECT_MAP[c]
    try:
        return int(code)
    except ValueError:
        return None


def _fmt(e):
    desc = e.get("description", "(no description)")
    proj_id = e.get("project_id")
    proj = PROJECT_NAMES.get(proj_id, str(proj_id) if proj_id else "")
    dur = e.get("duration", 0)
    start = e.get("start", "")
    stop = e.get("stop") or ""
    try:
        start = datetime.datetime.fromisoformat(start).astimezone(_tz()).strftime("%H:%M")
    except Exception:
        pass
    try:
        stop = datetime.datetime.fromisoformat(stop).astimezone(_tz()).strftime("%H:%M")
    except Exception:
        stop = "running"
    proj_str = f" @{proj}" if proj else ""
    dur_str = f"{abs(dur) // 60}min" if dur > 0 else "running"
    return f"{start}-{stop} {desc}{proj_str} ({dur_str}) [id:{e['id']}]"


def cmd_start(args):
    if not args:
        sys.exit("Usage: start <description> [project] [tags...] [--at HH:MM]")
    # Extract --at flag if present
    start_time = None
    if "--at" in args:
        at_idx = args.index("--at")
        if at_idx + 1 < len(args):
            from datetime import timezone
            hhmm = args[at_idx + 1]
            if ":" not in hhmm and len(hhmm) == 4:
                hhmm = hhmm[:2] + ":" + hhmm[2:]
            today = datetime.datetime.now(_tz()).strftime("%Y-%m-%d")
            local_dt = datetime.datetime.fromisoformat(f"{today}T{hhmm}:00")
            local_dt = local_dt.replace(tzinfo=_tz())
            start_time = local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            args = args[:at_idx] + args[at_idx + 2:]
    desc = args[0]
    project = args[1] if len(args) > 1 else ""
    tags = args[2:] or None
    project_id = _resolve_project(project)
    entry = toggl_api.start_timer(desc, project_id, tags, start_time=start_time)
    proj_name = PROJECT_NAMES.get(project_id, project) if project_id else ""
    proj_str = f" → {proj_name}" if proj_name else ""
    print(f"Started: {desc}{proj_str} [id:{entry['id']}]")


def cmd_stop(_args):
    current = toggl_api.get_current()
    if not current:
        print("No timer running.")
        return
    entry = toggl_api.stop_timer(current["id"])
    print(f"Stopped: {_fmt(entry)}")


def cmd_current(args):
    current = toggl_api.get_current()
    if "--json" in args:
        # Raw entry (real ISO "start", not a reconstructed HH:MM) for
        # callers that need to compute elapsed time correctly across a
        # midnight crossing or a backward clock jump (e.g. wakeup_server.py's
        # freshness check) — parsing _fmt's human text loses the timestamp's
        # actual date and can't tell "started earlier today" from "the clock
        # moved backward since it started."
        print(json.dumps(current or {}))
        return
    if not current:
        print("No timer running.")
        return
    print(f"Running: {_fmt(current)}")


def cmd_today(_args):
    today = datetime.datetime.now(_tz()).date()
    raw = toggl_api.get_entries(
        start_date=(today - datetime.timedelta(days=1)).isoformat(),
        end_date=(today + datetime.timedelta(days=2)).isoformat(),
    ) or []
    entries = []
    for e in raw:
        try:
            st = datetime.datetime.fromisoformat(e.get("start", "")).astimezone(_tz())
        except Exception:
            continue
        if st.date() == today:
            entries.append(e)
        elif st.date() < today:
            # Cross-midnight entry (e.g. an overnight 睡觉 timer stopped this
            # morning): started before today but ends today / still running.
            # Include it clipped to 00:00 — the day-barrier view — so today's
            # coverage (gap checks, etc.) sees the morning portion.
            stop_raw = e.get("stop")
            try:
                en = (datetime.datetime.fromisoformat(stop_raw).astimezone(_tz())
                      if stop_raw else datetime.datetime.now(_tz()))
            except Exception:
                continue
            if en.date() >= today:
                midnight = datetime.datetime.combine(today, datetime.time(0, 0), tzinfo=_tz())
                clipped = dict(e)
                clipped["start"] = midnight.isoformat()
                if e.get("duration", 0) > 0:
                    clipped["duration"] = max(0, int((en - midnight).total_seconds()))
                entries.append(clipped)
    entries.sort(key=lambda e: e.get("start", ""))
    total = sum(e.get("duration", 0) for e in entries if e.get("duration", 0) > 0)
    for e in entries:
        print(_fmt(e))
    h, m = total // 3600, (total % 3600) // 60
    print(f"Total: {h}h {m}min across {len(entries)} entries")


def cmd_create(args):
    if len(args) < 3:
        sys.exit("Usage: create <description> <HH:MM|HHMM> <HH:MM|HHMM> [project] [tags...] [--tag TAG]... [--date YYYY-MM-DD]")
    desc, start_str, end_str = args[0], args[1], args[2]
    project = ""
    project_consumed = False
    tags = []
    ref_date = datetime.datetime.now(_tz()).date()
    i = 3
    while i < len(args):
        if args[i] == "--date" and i + 1 < len(args):
            ref_date = datetime.date.fromisoformat(args[i + 1])
            i += 2
        elif args[i] == "--tag" and i + 1 < len(args):
            # Unambiguous tag flag — use this when passing tags without a
            # project, since bare positional args always fill project first.
            tags.append(args[i + 1])
            i += 2
        elif not project_consumed:
            project = args[i]
            project_consumed = True
            i += 1
        else:
            tags.append(args[i])
            i += 1

    def parse_t(t):
        t = t.replace(":", "")
        h, m = int(t[:2]), int(t[2:4])
        return datetime.datetime(ref_date.year, ref_date.month, ref_date.day, h, m, tzinfo=_tz())

    start_dt = parse_t(start_str)
    end_dt = parse_t(end_str)
    if end_dt <= start_dt:
        end_dt += datetime.timedelta(days=1)

    # Split at midnight if needed
    midnight = datetime.datetime(start_dt.year, start_dt.month, start_dt.day, 23, 59, tzinfo=_tz())
    next_min = midnight + datetime.timedelta(minutes=1)
    project_id = _resolve_project(project)
    tag_list = tags or None

    if start_dt.date() != end_dt.date():
        dur1 = int((midnight - start_dt).total_seconds())
        dur2 = int((end_dt - next_min).total_seconds())
        e1 = toggl_api.create_entry(desc, start_dt.isoformat(), midnight.isoformat(), dur1, project_id, tag_list)
        e2 = toggl_api.create_entry(desc, next_min.isoformat(), end_dt.isoformat(), dur2, project_id, tag_list)
        print(f"Created (split): {_fmt(e1)}")
        print(f"          cont.: {_fmt(e2)}")
    else:
        dur = int((end_dt - start_dt).total_seconds())
        entry = toggl_api.create_entry(desc, start_dt.isoformat(), end_dt.isoformat(), dur, project_id, tag_list)
        print(f"Created: {_fmt(entry)}")


def cmd_delete(args):
    if not args:
        sys.exit("Usage: delete <entry_id>")
    toggl_api.delete_entry(int(args[0]))
    print(f"Deleted: {args[0]}")


COMMANDS = {
    "start": cmd_start,
    "stop": cmd_stop,
    "current": cmd_current,
    "today": cmd_today,
    "create": cmd_create,
    "delete": cmd_delete,
}

def _notify_tui():
    """Signal janus to refresh via SIGUSR1 after timer state changes."""
    try:
        from pathlib import Path
        pid = int((Path.home() / ".cache" / "janus.pid").read_text().strip())
        os.kill(pid, signal.SIGUSR1)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass


# Commands that change timer state and should trigger janus refresh
_MUTATING_COMMANDS = {"start", "stop", "create", "delete"}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: toggl_cli.py <{'|'.join(COMMANDS)}> [args...]")
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
    if sys.argv[1] in _MUTATING_COMMANDS:
        _notify_tui()
