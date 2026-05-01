#!/usr/bin/env python3
"""d357-watchdog — fires macOS notifications when a /d357 recording overruns or crashes.

Reads ~/.claude/skills/d357/state.json. Two failure modes covered:
  1. Recording process died (pid set, but pid not alive) — meet.py crashed.
  2. Recording overdue (pid alive, elapsed >= threshold).

Threshold: 2 × calendar_minutes (if known) else 90 minutes.
Notification rate-limit: at most once per 30 min once threshold is crossed.

Run by launchd (com.jm.d357-watchdog) every 10 min. Idempotent. Silent when
no recording is active.
"""

import json
import os
import subprocess as sp
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path.home() / ".claude" / "skills" / "d357" / "state.json"
NOTIFY_INTERVAL_SEC = 30 * 60
DEFAULT_THRESHOLD_MIN = 90


def notify(title: str, body: str) -> None:
    sp.run(
        ["osascript", "-e",
         f'display notification "{body}" with title "{title}" sound name "Funk"'],
        capture_output=True, timeout=5,
    )


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours
    return True


def main() -> None:
    if not STATE_FILE.exists():
        return
    try:
        state = json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return

    pid = state.get("pid")
    if not pid:
        return

    name = state.get("name", "?")
    started_iso = state.get("started")
    cal_minutes = state.get("calendar_minutes")
    last_warned_iso = state.get("last_warned_at")

    try:
        started = datetime.fromisoformat(started_iso)
    except (TypeError, ValueError):
        return

    now = datetime.now(timezone.utc).astimezone(started.tzinfo or timezone.utc)
    elapsed_sec = (now - started).total_seconds()

    if not pid_alive(pid):
        # Don't rate-limit crash notifications — important to surface immediately
        notify("d357 recording crashed",
               f"{name} (pid {pid}) is dead. Run /d357 stop to clean state.")
        return

    threshold_sec = (cal_minutes * 2 * 60) if cal_minutes else (DEFAULT_THRESHOLD_MIN * 60)
    if elapsed_sec < threshold_sec:
        return

    if last_warned_iso:
        try:
            last_warned = datetime.fromisoformat(last_warned_iso)
            if (now - last_warned).total_seconds() < NOTIFY_INTERVAL_SEC:
                return
        except ValueError:
            pass

    notify("d357 still recording",
           f"{name} at {int(elapsed_sec // 60)}min — /d357 stop?")
    state["last_warned_at"] = now.isoformat()
    try:
        STATE_FILE.write_text(json.dumps(state) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
