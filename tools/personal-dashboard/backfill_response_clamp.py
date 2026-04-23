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


def backfill_imsg(db_path: Path):
    """The iMessage DB stores recv_time/sent_time as ISO local strings.
    We assume LOCAL_TZ = America/Los_Angeles for those (matches scan_chatdb)."""
    if not db_path.exists():
        print(f"  skip: {db_path} not found")
        return
    from zoneinfo import ZoneInfo
    LOCAL = ZoneInfo("America/Los_Angeles")
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT id, recv_time, sent_time, response_hours, day FROM response_pairs"
    ).fetchall()
    updated = 0
    for row_id, recv_str, sent_str, old_hours, _day in rows:
        try:
            recv = datetime.fromisoformat(recv_str).replace(tzinfo=LOCAL)
            sent = datetime.fromisoformat(sent_str).replace(tzinfo=LOCAL)
        except Exception:
            continue
        new_hours = round(clamp_response_hours_dt(sent, recv), 2)
        if new_hours != old_hours:
            conn.execute(
                "UPDATE response_pairs SET response_hours = ? WHERE id = ?",
                (new_hours, row_id),
            )
            updated += 1

    # Rebuild daily_stats aggregates so the dashboard reads the new numbers.
    rebuilt = 0
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT day FROM response_pairs"
    ).fetchall()]
    for day in days:
        pairs = conn.execute(
            "SELECT response_hours, recv_time FROM response_pairs WHERE day = ?",
            (day,),
        ).fetchall()
        all_h = [p[0] for p in pairs if p[0] is not None]
        if not all_h:
            continue
        # Daytime subset = recv hour in [6, 21)
        dt_h = []
        for h, rt in pairs:
            try:
                hour = datetime.fromisoformat(rt).hour
                if 6 <= hour < 21:
                    dt_h.append(h)
            except Exception:
                pass
        def _med(xs):
            xs = sorted(xs)
            n = len(xs)
            if n == 0:
                return None
            return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 2)
        avg_h = round(sum(all_h) / len(all_h), 2)
        med_h = _med(all_h)
        avg_h_dt = round(sum(dt_h) / len(dt_h), 2) if dt_h else None
        med_h_dt = _med(dt_h) if dt_h else None
        conn.execute(
            "UPDATE daily_stats SET response_count = ?, avg_response_hours = ?, "
            "median_response_hours = ?, response_count_daytime = ?, "
            "avg_response_hours_daytime = ?, median_response_hours_daytime = ? "
            "WHERE day = ?",
            (len(all_h), avg_h, med_h, len(dt_h), avg_h_dt, med_h_dt, day),
        )
        rebuilt += 1
    conn.commit()
    conn.close()
    print(f"  imsg-responses.db: {updated} pairs clamped, {rebuilt} daily aggregates rebuilt (out of {len(rows)} pairs)")


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
    print("Backfilling iMessage response DB...")
    backfill_imsg(Path.home() / "vault" / "i447" / "i446" / "imsg-responses.db")
    print("Done.")


if __name__ == "__main__":
    main()
