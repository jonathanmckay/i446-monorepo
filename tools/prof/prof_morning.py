#!/usr/bin/env python3
"""prof_morning.py — R3b pre-meeting brief.

Lists today's meetings with R3a/R6 status (preread + agenda detection) so JM
can spot prep gaps BEFORE walking into the meeting. Intended for a morning
batch (e.g., 7am) or ad-hoc before any meeting block.

Usage:
  prof_morning.py            # today
  prof_morning.py --date 2026-05-29
  prof_morning.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prof_snapshot import fetch_events, normalize_event  # type: ignore
from prof_score import AGENDA_MIN_PREVIEW, PREREAD_MIN_PREVIEW, _should_score, MY_EMAIL_DEFAULT  # type: ignore


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p.add_argument("--my-email", default=MY_EMAIL_DEFAULT)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()

    raw = fetch_events(target)
    events = [normalize_event(e, args.my_email) for e in raw]
    events = [e for e in events if _should_score(e)[0]]
    events.sort(key=lambda e: e.get("start", ""))

    rows = []
    for e in events:
        body_len = e.get("body_preview_len", 0)
        is_organizer = e.get("is_organizer", False)
        has_agenda = body_len >= AGENDA_MIN_PREVIEW
        has_preread = body_len >= PREREAD_MIN_PREVIEW
        flags = []
        if is_organizer and not has_agenda:
            flags.append("R6 no-agenda (I run)")
        if not is_organizer and not has_preread:
            flags.append("R3a thin preread")
        rows.append({
            "start": (e.get("start") or "")[11:16],
            "end": (e.get("end") or "")[11:16],
            "subject": e.get("subject", ""),
            "is_organizer": is_organizer,
            "body_preview_len": body_len,
            "flags": flags,
        })

    if args.json:
        print(json.dumps({"date": target.isoformat(), "events": rows}, indent=2))
        return 0

    risky = [r for r in rows if r["flags"]]
    print(f"\n=== Prof morning brief — {target} ===")
    print(f"Meetings: {len(rows)}   Flagged: {len(risky)}\n")
    for r in rows:
        marker = "⚠" if r["flags"] else "·"
        org = "(I run) " if r["is_organizer"] else ""
        flag_str = ("  " + " | ".join(r["flags"])) if r["flags"] else ""
        print(f"  {marker} {r['start']}-{r['end']}  {r['subject'][:55]:55} {org}body={r['body_preview_len']}{flag_str}")
    print()
    if risky:
        print("Action: thicken agenda (you organize) or ping owner for preread (others).")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
