#!/usr/bin/env python3
"""log_arrival.py — append a /d357 lifecycle event to ~/.config/prof/arrivals.jsonl

Used by the /d357 skill to emit start/stop events for the professionalism
daemon (R2 arrival, R5 end-on-time).

Usage:
  log_arrival.py start --name "Francois 1:1" --calendar-minutes 30 \
                       --scheduled-start 2026-05-28T09:00:00-07:00
  log_arrival.py stop  --name "Francois 1:1"

Fields:
  ts                  ISO8601 with offset, wall clock at log time
  kind                "start" | "stop"
  name                meeting name (from /d357 input or calendar title)
  calendar_minutes    optional, only on start
  scheduled_start     optional, only on start (ISO8601 of the calendar event)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(os.environ.get("PROF_ARRIVALS_LOG", str(Path.home() / ".config/prof/arrivals.jsonl")))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["start", "stop"])
    p.add_argument("--name", required=True)
    p.add_argument("--calendar-minutes", type=int, default=None)
    p.add_argument("--scheduled-start", default=None,
                   help="ISO8601 with offset (e.g. 2026-05-28T09:00:00-07:00)")
    args = p.parse_args()

    record = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "kind": args.kind,
        "name": args.name,
    }
    if args.calendar_minutes is not None:
        record["calendar_minutes"] = args.calendar_minutes
    if args.scheduled_start:
        record["scheduled_start"] = args.scheduled_start

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")

    print(f"prof: logged {args.kind} for {args.name!r} → {LOG_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
