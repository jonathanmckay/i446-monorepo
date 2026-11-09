#!/usr/bin/env python3
"""1st hci photo proof: was a selfie taken in the last N hours?

Spawned in the background by did-fast after the `1st hci` 0neon card closes
(JM 2026-11-09). Reads the System Photo Library on Ix (external "Ix Photos" volume) read-only, looks for a
photo created inside the window that Photos detected a face in (front-camera
lens preferred), and with --apply docks the 1st hci credit by appending -5 to
today's 0分!个 through the excel-http daemon when there is none.

Statuses
  selfie     a face photo inside the window → full credit, no write
  none       library is live but has no such photo → -5 with --apply
  no-source  library has not received any asset in --live-days days, so
             it cannot know (iCloud Photos is not syncing to this Mac) →
             no write unless --dock-when-stale; logged so the gap is visible

Every run appends one JSON line to ~/.local/state/jm/1st-hci-selfie.jsonl.

Usage: selfie_check.py [--hours 2] [--apply] [--points 5] [--date M/D]
                       [--db PATH] [--live-days 7] [--dock-when-stale]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))

# The System Photo Library (iCloud-synced, 47k assets) lives on the external
# "Ix Photos" volume; ~/Pictures holds an old 720-asset library that iCloud
# never fed. First existing path wins (JM 2026-11-09).
PHOTOS_DB_CANDIDATES = [
    Path("/Volumes/Ix Photos/Photos Library.photoslibrary/database/Photos.sqlite"),
    Path.home() / "Pictures/Photos Library.photoslibrary/database/Photos.sqlite",
]


def default_db() -> Path:
    for c in PHOTOS_DB_CANDIDATES:
        if c.exists():
            return c
    return PHOTOS_DB_CANDIDATES[0]
LOG = Path.home() / ".local/state/jm/1st-hci-selfie.jsonl"
APPLE_EPOCH = 978307200  # 2001-01-01 → unix


def _snapshot(db: Path, tmpdir: str) -> Path:
    """Fallback only: copy db + wal + shm when an in-place read-only open is
    refused (immutable=1 would skip the WAL and miss today's assets)."""
    dst = Path(tmpdir) / "Photos.sqlite"
    shutil.copy2(db, dst)
    for suffix in ("-wal", "-shm"):
        src = Path(str(db) + suffix)
        if src.exists():
            shutil.copy2(src, Path(str(dst) + suffix))
    return dst


def query(db: Path, since: dt.datetime, now: dt.datetime | None = None) -> dict:
    """Return {newest_added, newest_created, candidates:[{created, lens, faces, filename}]}
    for photos (ZKIND=0, not trashed/hidden) created at or after `since`."""
    now = now or dt.datetime.now().astimezone()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        newest_added, newest_created = con.execute(
            "select max(ZADDEDDATE), max(ZDATECREATED) from ZASSET").fetchone()
        since_apple = since.timestamp() - APPLE_EPOCH
        rows = con.execute(
            """
            select a.ZDATECREATED, e.ZLENSMODEL, a.ZFILENAME,
                   (select count(*) from ZDETECTEDFACE f where f.ZASSETFORFACE = a.Z_PK)
            from ZASSET a
            left join ZEXTENDEDATTRIBUTES e on e.Z_PK = a.ZEXTENDEDATTRIBUTES
            where a.ZDATECREATED >= ?
              and a.ZKIND = 0
              and coalesce(a.ZTRASHEDSTATE, 0) = 0
              and coalesce(a.ZHIDDEN, 0) = 0
            order by a.ZDATECREATED desc
            """,
            (since_apple,),
        ).fetchall()
    finally:
        con.close()

    def _ts(v):
        return (dt.datetime.fromtimestamp(v + APPLE_EPOCH).astimezone().isoformat(timespec="seconds")
                if v is not None else None)

    cands = [{"created": _ts(c), "lens": lens or "", "filename": fn or "", "faces": faces or 0}
             for c, lens, fn, faces in rows]
    return {"newest_added": _ts(newest_added), "newest_created": _ts(newest_created),
            "candidates": cands}


def is_selfie(c: dict) -> bool:
    """A face photo; front-camera lens is the strong signal, unknown lens is
    accepted (iCloud-synced assets often lack EXIF until downloaded)."""
    if not c.get("faces"):
        return False
    lens = (c.get("lens") or "").lower()
    return "front" in lens or lens == "" or "back" not in lens


def decide(q: dict, now: dt.datetime, live_days: int) -> tuple[str, dict | None]:
    selfies = [c for c in q["candidates"] if is_selfie(c)]
    if selfies:
        return "selfie", selfies[0]
    newest = q.get("newest_added")
    if newest:
        age = now - dt.datetime.fromisoformat(newest)
        if age <= dt.timedelta(days=live_days):
            return "none", None
    return "no-source", None


def dock(points: int, date_md: str) -> dict:
    from neon import cols as neon_cols
    from neon import excel as neon_excel
    col = neon_cols.domain_col("0分", "个")
    return neon_excel.append("0分", col, date=date_md, value=f"-{points}",
                             src="1st hci: no selfie in window")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--apply", action="store_true", help="append -points to 0分!个 when status is none")
    ap.add_argument("--points", type=int, default=5)
    ap.add_argument("--date", help="M/D row (default today)")
    ap.add_argument("--db", default=str(default_db()))
    ap.add_argument("--live-days", type=int, default=7,
                    help="library counts as live if any asset was added within this many days")
    ap.add_argument("--dock-when-stale", action="store_true",
                    help="also dock on no-source (default: a library that cannot know does not dock)")
    ap.add_argument("--log", default=str(LOG))
    a = ap.parse_args(argv)

    now = dt.datetime.now().astimezone()
    since = now - dt.timedelta(hours=a.hours)
    date_md = a.date or f"{now.month}/{now.day}"
    rec = {"ts": now.isoformat(timespec="seconds"), "date": date_md, "hours": a.hours,
           "status": None, "match": None, "docked": 0, "newest_added": None, "error": None}
    try:
        db = Path(a.db)
        if not db.exists():
            raise FileNotFoundError(str(db))
        try:
            # In place: the 1.7 GB system library reads in ~0.1 s and the WAL
            # is honoured, so today's assets are visible.
            q = query(db, since, now)
        except sqlite3.OperationalError:
            with tempfile.TemporaryDirectory() as td:
                q = query(_snapshot(db, td), since, now)
        rec["newest_added"] = q["newest_added"]
        status, match = decide(q, now, a.live_days)
        rec["status"], rec["match"] = status, match
        should_dock = a.apply and (status == "none" or (status == "no-source" and a.dock_when_stale))
        if should_dock:
            r = dock(a.points, date_md)
            rec["docked"] = a.points
            rec["write"] = r
    except Exception as e:  # noqa: BLE001 - never crash the caller's background job
        rec["status"] = rec["status"] or "error"
        rec["error"] = f"{type(e).__name__}: {e}"

    try:
        Path(a.log).parent.mkdir(parents=True, exist_ok=True)
        with open(a.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass

    m = rec["match"]
    detail = f" ({m['created']} {m['lens'] or 'lens?'} faces={m['faces']})" if m else ""
    print(f"1st hci selfie: {rec['status']}{detail}"
          + (f" → 0分!个 -{rec['docked']} on {date_md}" if rec["docked"] else "")
          + (f" · newest asset added {rec['newest_added']}" if rec["status"] == "no-source" else "")
          + (f" · {rec['error']}" if rec["error"] else ""))
    return 0 if rec["status"] in ("selfie", "none", "no-source") else 1


if __name__ == "__main__":
    sys.exit(main())
