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

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

CLI = str(Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py")
CACHE = str(Path.home() / ".claude/skills/tg/cache.json")
DO_SESSION = Path.home() / ".claude/skills/do/active.json"
DID_FAST = str(Path.home() / "i446-monorepo/tools/did/did-fast.py")

# ── Shortcode table ──────────────────────────────────────────────────────────

SHORTCODES = {
    # High frequency
    "الفاتحة": ("hcm", []), "睡觉": ("睡觉", ["-3"]), "fall asleep": ("hcmc", ["-1"]),
    "0t": ("n156", []), "新闻": ("hcmc", ["-3"]), "work": ("i9", []),
    "family time": ("xk87", []), "read": ("xk87", ["-3"]), "0l": ("g245", []),
    "math": ("xk87", []), "冥想": ("hcm", []), "day hci": ("hci", []),
    "wake up": ("infra", []), "bball": ("hcbp", []), "其他人": ("hcm", []),
    "-1l": ("g245", []), "o314": ("hcm", ["-3"]), "hiit": ("hcbp", ["-2"]),
    "vibing": ("i9", []), "hcmr": ("hcm", []), "kn47 daily": ("m5x2", []),
    "0g": ("g245", []), "epcn": ("epcn", []), "h lunch": ("hcb", []),
    "meetings": ("i9", []), "tasks": ("i9", []), "ren to sleep": ("xk87", []),
    "1s": ("g245", []), "get up": ("infra", []), "词汇": ("hcmc", ["-3"]),
    "doze": ("hcmc", ["-1"]), "youtube": ("hcmc2", ["2"]), "stats": ("i9", []),
    "out the door": ("infra", []),
    # Medium frequency
    "h breakfast": ("hcb", []), "breakfast": ("hcb", []), "dinner": ("xk87", []),
    "lunch": ("hcb", []), "h dinner": ("hcb", []), "dad call": ("家", []),
    "lx walk": ("xk88", []), "r203 weekly": ("m5x2", []), "r202 weekly": ("m5x2", []),
    "kids to sleep": ("xk87", []), "lego": ("xk87", []), "notes": ("i9", []),
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
}

# Domain-only shortcodes
DOMAINS = {
    "hcm", "hcmc", "hcb", "hcbp", "hci", "i9", "m5x2", "xk87", "xk88",
    "s897", "epcn", "g245", "n156", "i444", "infra", "家", "睡觉",
}

# Pattern: "1 <domain>" maps to that domain
_ONE_PREFIX = re.compile(r'^1\s+(\S+)$', re.IGNORECASE)


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


def resolve(raw: str):
    """Return (description, project, tags, @override_used)."""
    desc = raw.strip()
    project = ""
    tags = []
    override = False

    # Extract @project override
    m = re.search(r'\s@(\S+)\s*$', desc)
    if m:
        project = m.group(1)
        desc = desc[:m.start()].strip()
        override = True

    if not override:
        key = desc.lower()
        # Exact shortcode match
        if key in SHORTCODES:
            project, tags = SHORTCODES[key]
        # Domain-only
        elif key in DOMAINS:
            project = key
            desc = ""
        # "1 <domain>" pattern
        else:
            pm = _ONE_PREFIX.match(desc)
            if pm and pm.group(1).lower() in DOMAINS:
                project = pm.group(1).lower()

    return desc, project, tags


def cmd_start(desc, project, tags):
    args = ["start", desc] if desc else ["start", project]
    if project and desc:
        args.append(project)
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
    args = ["create", desc, start_t, end_t]
    if project:
        args.append(project)
    out = _run_cli(*args)
    return out


def cmd_backdated(backtime, desc, project, tags):
    """Stop current, trim overlap, start backdated."""
    results = []
    # Stop current
    cur = _run_cli("current")
    if "Running:" in cur:
        stop_out = _run_cli("stop")
        results.append(stop_out)
    # Start backdated
    hhmm = backtime[:2] + ":" + backtime[2:]
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

    # Simple commands
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

    # Check for time range: "desc HH:MM-HH:MM" or "desc H-H"
    range_match = re.search(r'(\d{1,2}(?::\d{2})?)\s*-\s*(\d{1,2}(?::\d{2})?)\s*$', raw)
    if range_match:
        start_t = range_match.group(1)
        end_t = range_match.group(2)
        # Normalize to HH:MM
        if ":" not in start_t:
            start_t = start_t.zfill(2) + ":00"
        if ":" not in end_t:
            end_t = end_t.zfill(2) + ":00"
        desc_part = raw[:range_match.start()].strip()
        desc, project, tags = resolve(desc_part)
        print(cmd_create_range(desc or desc_part, project, tags, start_t, end_t))
        return

    # Check for backdated start: "HHMM desc"
    backdate_match = re.match(r'^(\d{4})\s+(.+)$', raw)
    if backdate_match:
        backtime = backdate_match.group(1)
        h, m = int(backtime[:2]), int(backtime[2:])
        if 0 <= h <= 23 and 0 <= m <= 59:
            rest = backdate_match.group(2)
            desc, project, tags = resolve(rest)
            print(cmd_backdated(backtime, desc, project, tags))
            return

    # Default: start timer
    desc, project, tags = resolve(raw)
    print(cmd_start(desc, project, tags))


if __name__ == "__main__":
    main()
