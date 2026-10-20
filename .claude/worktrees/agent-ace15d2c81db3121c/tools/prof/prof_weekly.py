#!/usr/bin/env python3
"""prof_weekly.py — R4 weekly camera-off self-report batch.

R4 is the only rule that can't be detected from Graph alone — camera state
isn't exposed. Self-report on a per-meeting basis is too noisy (the s897
rule says "don't ask every time"), so we batch: once a week, list all the
week's online meetings and let JM tick the ones where his camera was off.

Writes the marked list to ~/.config/prof/camera-off-YYYY-WW.json so the
weekly /1s review can roll them into the s897 sum.

Usage:
  prof_weekly.py                          # prior 7 days, interactive ticking
  prof_weekly.py --start 2026-05-22       # week starting this date
  prof_weekly.py --list                   # print, don't ask
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prof_snapshot import fetch_events, normalize_event  # type: ignore
from prof_score import _should_score, MY_EMAIL_DEFAULT  # type: ignore

OUT_DIR = Path.home() / ".config/prof"


def _is_online(ev: dict) -> bool:
    """Heuristic: meeting body has Teams join URL OR ≥2 attendees marked online."""
    # bodyPreview is normalized, but the original event has isOnlineMeeting.
    # normalize_event drops that — for v1, treat any meeting ≥2 attendees as online.
    return ev.get("attendee_count", 0) >= 2


def collect_week(start: date, my_email: str) -> list[dict]:
    rows = []
    for i in range(7):
        d = start + timedelta(days=i)
        for raw in fetch_events(d):
            ev = normalize_event(raw, my_email)
            if not _should_score(ev)[0]:
                continue
            if not _is_online(ev):
                continue
            rows.append({
                "date": d.isoformat(),
                "start": (ev.get("start") or "")[11:16],
                "subject": ev.get("subject", ""),
                "id": ev.get("id"),
                "attendees": ev.get("attendee_count"),
                "camera_off": False,
            })
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", help="YYYY-MM-DD (default: 7 days ago)")
    p.add_argument("--my-email", default=MY_EMAIL_DEFAULT)
    p.add_argument("--list", action="store_true", help="print only, no prompts")
    args = p.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start \
        else date.today() - timedelta(days=7)

    rows = collect_week(start, args.my_email)
    iso_year, iso_week, _ = start.isocalendar()
    print(f"\n=== R4 camera-off review — week of {start} ({len(rows)} online meetings) ===\n")

    for i, r in enumerate(rows):
        print(f"  [{i:2d}] {r['date']} {r['start']}  {r['subject'][:60]}")

    if args.list:
        return 0

    print("\nEnter comma-separated indices where camera was OFF (or blank for none):")
    raw = input("> ").strip()
    marked = set()
    if raw:
        for tok in raw.split(","):
            tok = tok.strip()
            if tok.isdigit():
                marked.add(int(tok))

    for i, r in enumerate(rows):
        r["camera_off"] = i in marked

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"camera-off-{iso_year}-W{iso_week:02d}.json"
    out_path.write_text(json.dumps({
        "week_start": start.isoformat(),
        "marked_count": len(marked),
        "total_meetings": len(rows),
        "rule": "R4",
        "penalty_per_marked": -5,
        "sum_points": -5 * len(marked),
        "meetings": rows,
    }, indent=2))
    print(f"\nWrote {out_path}")
    print(f"R4 weekly deduction: {-5 * len(marked):+d} points ({len(marked)} camera-off)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
