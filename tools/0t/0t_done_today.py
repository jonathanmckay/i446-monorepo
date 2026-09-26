#!/usr/bin/env python3
"""Is the 0t habit already done today?  exit 0 = done, 1 = not done, 2 = error.

Gate for ~/bin/run_0t.sh (the Ix cron safety net). Two independent signals;
either one is enough to call it done:

  1. completed-today.json on this host, AFTER absorbing the other hosts'
     Syncthing-synced mirrors (mark-completed.py --absorb-remote). This is the
     same guard 0t-fast.mark_done() uses. It is blind while Syncthing is down
     (2026-09-26: Straylight's disk was <1% free and Syncthing had stopped
     scanning for 30h, so Ix never saw Straylight's completions).
  2. Todoist: the daily '0t' habit (label `0t`, content starts with "0t") is
     recurring, so closing it advances its due date past today. Network-only,
     needs no host sync. Silently skipped if Todoist is unreachable or the
     token file is missing (~/.config/todoist/token — see lib/todoist.py).

Prints one line per signal so the cron log shows WHY it skipped or ran.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

MONO = Path.home() / "i446-monorepo"
sys.path.insert(0, str(MONO / "lib"))
import daytime  # noqa: E402

HABIT = "0t"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def completed_today_says_done() -> bool:
    mc = _load("mark_completed", MONO / "tools/did/mark-completed.py")
    try:
        absorbed = mc.absorb_remote()
    except Exception as e:  # never let a bad mirror block the gate
        absorbed = f"absorb failed: {e}"
    hit = mc.is_duplicate_today(HABIT)
    print(f"completed-today: {'done' if hit else 'not done'} (absorbed={absorbed})")
    return hit is not None


def todoist_says_done(today: str) -> bool | None:
    """True/False from the recurring task's due date, None if it can't tell."""
    try:
        import todoist  # lib/todoist.py
        page = todoist._request("GET", f"/tasks?label={HABIT}&limit=50") or {}
    except Exception as e:
        print(f"todoist: unavailable ({str(e)[:120]})")
        return None
    tasks = page.get("results", page) if isinstance(page, dict) else page
    for t in tasks or []:
        content = (t.get("content") or "").strip()
        if not re.match(r"^0t\b", content):
            continue
        due = (t.get("due") or {}).get("date")
        if not due:
            print(f"todoist: {content!r} has no due date; can't tell")
            return None
        done = due[:10] > today
        print(f"todoist: {content!r} due {due[:10]} vs today {today} -> "
              f"{'done' if done else 'not done'}")
        return done
    print("todoist: no open task labelled 0t; can't tell")
    return None


def main() -> int:
    today = daytime.today_iso()
    if completed_today_says_done():
        return 0
    if todoist_says_done(today):
        return 0
    print(f"0t not done yet today ({today})")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"0t_done_today error: {e}", file=sys.stderr)
        sys.exit(2)
