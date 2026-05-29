#!/usr/bin/env python3
"""prof_snapshot.py — snapshot today/tomorrow's Outlook calendar via Agency MCP.

Used by the professionalism daemon to detect same-day reschedules (R1):
compare the snapshot taken at 3am against the live calendar later in the day.

Snapshots land at ~/.config/prof/cal-YYYY-MM-DD.json.

Usage:
  prof_snapshot.py            # snapshot tomorrow (default — run at 3am via cron)
  prof_snapshot.py --today    # snapshot today (for ad-hoc baseline)
  prof_snapshot.py --date 2026-05-29
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ibx"))
import agency_mcp  # type: ignore

SNAPSHOT_DIR = Path.home() / ".config/prof"


def _local_iso(d: date, hour: int, minute: int) -> str:
    """Build ISO8601 with local tz offset for the given date + clock time."""
    naive = datetime.combine(d, datetime.min.time()).replace(hour=hour, minute=minute)
    return naive.astimezone().isoformat()


def fetch_events(target: date) -> list[dict]:
    start = _local_iso(target, 0, 0)
    end = _local_iso(target, 23, 59)
    res = agency_mcp.call_tool(
        "calendar",
        "ListCalendarView",
        {"startDateTime": start, "endDateTime": end},
        timeout=60,
    )
    content = res.get("content", [])
    if not content:
        return []
    # First text block is the human prefix; find the JSON block.
    for block in content:
        text = block.get("text", "")
        if text.startswith("{") or '"value"' in text:
            try:
                # The format is "Calendar view retrieved successfully.\n{...json...}"
                json_start = text.find("{")
                if json_start >= 0:
                    payload = json.loads(text[json_start:])
                    return payload.get("value", [])
            except json.JSONDecodeError:
                continue
    return []


def normalize_event(ev: dict, my_email: str) -> dict:
    """Reduce a Graph event to the fields the prof scorer needs."""
    organizer_addr = (
        ev.get("organizer", {}).get("emailAddress", {}).get("address", "")
    ).lower()
    attendees = ev.get("attendees", []) or []
    body_preview = ev.get("bodyPreview", "") or ""
    return {
        "id": ev.get("id"),
        "subject": ev.get("subject", ""),
        "start": ev.get("start", {}).get("dateTime"),
        "end": ev.get("end", {}).get("dateTime"),
        "is_cancelled": bool(ev.get("isCancelled")),
        "is_online_meeting": bool(ev.get("isOnlineMeeting")),
        "organizer": organizer_addr,
        "is_organizer": organizer_addr == my_email.lower(),
        "attendee_count": len(attendees),
        "response_status": (ev.get("responseStatus", {}) or {}).get("response", ""),
        "body_preview_len": len(body_preview),
        "type": ev.get("type", ""),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("--today", action="store_true")
    g.add_argument("--date", type=str, help="YYYY-MM-DD")
    p.add_argument("--my-email", default="jomckay@microsoft.com")
    args = p.parse_args()

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    elif args.today:
        target = date.today()
    else:
        target = date.today() + timedelta(days=1)

    events = fetch_events(target)
    normalized = [normalize_event(e, args.my_email) for e in events]

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOT_DIR / f"cal-{target.isoformat()}.json"
    payload = {
        "snapshot_taken_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "target_date": target.isoformat(),
        "my_email": args.my_email,
        "events": normalized,
    }
    out.write_text(json.dumps(payload, indent=2))
    print(f"prof_snapshot: wrote {len(normalized)} events → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
