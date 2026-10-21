"""toggl_store — read-only access to JM's SQLite store of Toggl entries.

The store (~/vault/i156/toggl.sqlite, see tools/toggl-db/toggl_db.py and
vault/i156/i156.md) is written nightly on Ix and copied here by Syncthing.
This module answers the one question every consumer has — "the entries for
these local days" — in the same v9 shape toggl_api.get_entries returns, so
callers need not know which source served them.

A day is SETTLED when it is at least one full day before the last completed
sync: the nightly 05:10 sync re-pulls the last 14 days, so everything up to
yesterday is complete by then, and edits made to older entries are picked
up by the Sunday 120-day and monthly 400-day sweeps.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HOME_TZ = ZoneInfo("America/Los_Angeles")
STORE_PATH = Path(os.environ.get("TOGGL_DB") or (Path.home() / "vault" / "i156" / "toggl.sqlite"))
FIRST_DAY = date(2018, 3, 28)       # first real entry; nothing older exists in Toggl
WORKSPACE_ID = 2092616


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def settled_until(path: Path = STORE_PATH) -> date | None:
    """Last local day the store is complete for, or None without a store."""
    conn = _connect(path)
    if conn is None:
        return None
    try:
        ts = conn.execute("SELECT MAX(fetched_at) FROM sync_log WHERE complete = 1").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not ts:
        return None
    return datetime.fromtimestamp(ts, HOME_TZ).date() - timedelta(days=1)


def covers(start: date, end: date, path: Path = STORE_PATH) -> bool:
    """True when every local day in [start, end] is settled in the store."""
    if os.environ.get("TOGGL_LIVE"):
        return False
    until = settled_until(path)
    return until is not None and start >= FIRST_DAY and end <= until and start <= end


def entries(start: date, end: date, path: Path = STORE_PATH) -> list[dict]:
    """Entries whose local day is in [start, end], v9-shaped (id, description,
    start, stop, duration, project_id, tags as names, tag_ids, at,
    workspace_id), oldest first. Running entries never exist here."""
    conn = _connect(path)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, description, start, stop, seconds, project_id, tag_ids, tags, at "
            "FROM entries WHERE day BETWEEN ? AND ? ORDER BY start, id",
            (start.isoformat(), end.isoformat())).fetchall()
    finally:
        conn.close()
    out = []
    for eid, desc, st, sp, secs, pid, tag_ids, tags, at in rows:
        try:
            tids = [int(t) for t in json.loads(tag_ids or "[]")]
        except (ValueError, TypeError):
            tids = []
        out.append({"id": eid, "description": desc or "", "start": st, "stop": sp, "duration": int(secs),
                    "project_id": pid, "tags": json.loads(tags or "[]"), "tag_ids": tids, "at": at,
                    "workspace_id": WORKSPACE_ID, "_source": "store"})
    return out
