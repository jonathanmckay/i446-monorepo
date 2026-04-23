#!/usr/bin/env python3
"""Backfill response_hours in outlook + teams response DBs using the new
midnight-PST daily reset. iMessage is handled by re-running imsg_response_db.py
which rescans the last 30 days from chat.db."""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from comms_response_clamp import clamp_response_hours_dt

MATCH_WINDOW_HOURS = 72


def _parse(ts_str):
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def backfill(db_path: Path, table: str):
    if not db_path.exists():
        print(f"  skip: {db_path} not found")
        return
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        f"SELECT item_id, fetched_at, action_at, response_hours FROM {table} "
        f"WHERE fetched_at IS NOT NULL AND action_at IS NOT NULL"
    ).fetchall()
    updated = 0
    nulled = 0
    for item_id, fetched_at, action_at, old_hours in rows:
        fetched = _parse(fetched_at)
        sent = _parse(action_at)
        if not fetched or not sent:
            continue
        raw = (sent - fetched).total_seconds() / 3600
        if raw < 0 or raw > MATCH_WINDOW_HOURS:
            new_hours = None
        else:
            new_hours = round(clamp_response_hours_dt(sent, fetched), 2)
        if new_hours != old_hours:
            conn.execute(
                f"UPDATE {table} SET response_hours = ? WHERE item_id = ?",
                (new_hours, item_id),
            )
            if new_hours is None:
                nulled += 1
            else:
                updated += 1
    conn.commit()
    conn.close()
    print(f"  {db_path.name}: {updated} rows clamped, {nulled} nulled (out of {len(rows)})")


def main():
    print("Backfilling Outlook response DB...")
    backfill(
        Path.home() / ".config" / "outlook" / "response_times.db",
        "outlook_responses",
    )
    print("Backfilling Teams response DB...")
    backfill(
        Path.home() / ".config" / "teams" / "response_times.db",
        "teams_responses",
    )
    print("Done. (iMessage DB will refresh on next imsg_response_db.py run.)")


if __name__ == "__main__":
    main()
