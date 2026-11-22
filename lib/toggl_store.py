"""toggl_store — read-only access to JM's SQLite store of Toggl entries.

The store (~/vault/i156/toggl.sqlite, see tools/toggl-db/toggl_db.py and
vault/i156/i156.md) is written nightly on Ix and copied here by Syncthing.
This module answers the one question every consumer has — "the entries for
these local days" — in the same v9 shape toggl_api.get_entries returns, so
callers need not know which source served them.

A day is SETTLED when it is at least two full days before the last completed
sync: the nightly 05:10 sync re-pulls the last 14 days, and edits made to
older entries are picked up by the Sunday 120-day and monthly 400-day sweeps.
Yesterday is never settled: JM backfills the previous evening after the
05:10 sync (2026-10-22: 睡觉 21:15-23:59 logged that morning was missing from
the store, so /0t read a midnight bedtime, undercounted sleep by 164 min and
docked 30分 for a late night that didn't happen).
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
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
    return datetime.fromtimestamp(ts, HOME_TZ).date() - timedelta(days=2)


# Dirty days (2026-11-22). A first-party write to a settled day lands in Toggl
# but not in the store, so every reader kept serving the day without it until
# the 05:10 sync: JM filled 19:03-20:30 on 11/19 from janus, saw nothing
# change, retried, and got three copies (trim_range reads the same store, so
# it could not see the entries it had just created either). The writer now
# marks the day; a marked day is read live until a sync re-pulls it.
#
# One sidecar per host beside the store, so Syncthing carries a mark made on
# one machine to readers on the others and no two hosts write the same file.
# A sync clears a mark only when it finished DIRTY_MARGIN_S after the write:
# sync_log.fetched_at is stamped after the fetch returns, so a write landing
# mid-sync (or a skewed clock on another host) must stay dirty.
DIRTY_MARGIN_S = 2 * 3600
_REAL_STORE = Path.home() / "vault" / "i156" / "toggl.sqlite"


def _dirty_file(path: Path) -> Path:
    host = socket.gethostname().split(".")[0].lower() or "host"
    return path.with_name(f"toggl-dirty.{host}.json")


def _read_marks(f: Path) -> dict[str, float]:
    try:
        raw = json.loads(f.read_text())
        return {str(k): float(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _resynced(conn: sqlite3.Connection, day: str, ts: float) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sync_log WHERE complete = 1 AND window_start <= ? AND window_end >= ? "
        "AND fetched_at > ? LIMIT 1", (day, day, ts + DIRTY_MARGIN_S)).fetchone()
    return row is not None


def _live_marks(marks: dict[str, float], path: Path) -> dict[str, float]:
    """The marks no completed sync has re-pulled yet."""
    if not marks:
        return {}
    conn = _connect(path)
    if conn is None:
        return marks
    try:
        return {d: ts for d, ts in marks.items() if not _resynced(conn, d, ts)}
    except sqlite3.Error:
        return marks
    finally:
        conn.close()


def dirty_days(path: Path = STORE_PATH) -> set[date]:
    """Local days written since the store last pulled them, on any host."""
    merged: dict[str, float] = {}
    for f in path.parent.glob("toggl-dirty.*.json"):
        for d, ts in _read_marks(f).items():
            merged[d] = max(ts, merged.get(d, 0.0))
    out = set()
    for d in _live_marks(merged, path):
        try:
            out.add(date.fromisoformat(d))
        except ValueError:
            pass
    return out


def mark_dirty(day: date, path: Path = STORE_PATH) -> bool:
    """Record a first-party write to `day`. Only a settled day needs it (any
    other day is read live already), and without a store there is nothing to
    go stale: both are no-ops, which also keeps tests that fake a write from
    touching the real sidecar. Never raises."""
    try:
        if os.environ.get("PYTEST_CURRENT_TEST") and Path(path) == _REAL_STORE:
            return False   # a test faking a write must never mark JM's real store
        until = settled_until(path)
        if until is None or day > until or day < FIRST_DAY:
            return False
        f = _dirty_file(path)
        marks = _live_marks(_read_marks(f), path)   # prune this host's cleared marks
        marks[day.isoformat()] = time.time()
        tmp = f.with_name(f.name + f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps(marks, sort_keys=True))
        os.replace(tmp, f)
        return True
    except Exception:  # noqa: BLE001
        return False


def day_of(entry_id: int, path: Path = STORE_PATH) -> date | None:
    """The stored local day of an entry, or None when the store lacks it."""
    conn = _connect(path)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT day FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return date.fromisoformat(row[0]) if row else None
    except (sqlite3.Error, ValueError, TypeError):
        return None
    finally:
        conn.close()


def covers(start: date, end: date, path: Path = STORE_PATH, ignore_dirty: bool = False) -> bool:
    """True when every local day in [start, end] is settled in the store and
    none was written since its last pull (see dirty_days)."""
    if os.environ.get("TOGGL_LIVE"):
        return False
    until = settled_until(path)
    if not (until is not None and start >= FIRST_DAY and end <= until and start <= end):
        return False
    if ignore_dirty:
        return True
    return not any(start <= d <= end for d in dirty_days(path))


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
