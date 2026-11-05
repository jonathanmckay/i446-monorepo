"""分 per Toggl entry, shared by every client and host (JM 2026-11-05: "task <>
time entry <> minutes used <> 分 achieved").

One append-only file per host in the Syncthing-synced vault, as janus_events
does it: ~/vault/z_meta/events/entry-points.<host>.jsonl. Each host writes only
its own file, so Syncthing has nothing to conflict on, and a reader unions them
with the newest line winning per id. A line with pts null is a tombstone: the
record was undone or merged away, and it beats an older value, so a local
sidecar or another host's line can't bring the points back.

did-fast writes the entries it created or stopped; janus and mobile write what
only they know (^G corrections, pins). Never raises: an entry's points must not
cost a completion.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

HOST = os.uname().nodename.split(".")[0].lower()
_cache: dict = {"key": None, "days": {}}


def _dir() -> Path | None:
    env = os.environ.get("ENTRY_POINTS_DIR")
    if env:
        return Path(env)
    if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    return Path.home() / "vault" / "z_meta" / "events"


def _iso(day) -> str:
    return day.isoformat() if isinstance(day, dt.date) else str(day)


def record(day, points: dict, src: str = "") -> None:
    """Append {entry id: 分, or None to clear it} for `day`."""
    d = _dir()
    if d is None or not points:
        return
    now = dt.datetime.now().isoformat(timespec="seconds")
    lines = [json.dumps({"ts": now, "day": _iso(day), "id": str(k), "pts": v, "src": src},
                        ensure_ascii=False) for k, v in points.items()]
    try:
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"entry-points.{HOST}.jsonl", "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def _load(d: Path) -> dict:
    """{day: {id: pts or None}}, re-read only when a host's file changed:
    janus asks once per row per render."""
    try:
        files = sorted(d.glob("entry-points.*.jsonl"))
        key = (str(d), tuple((str(p), p.stat().st_mtime_ns) for p in files))
    except OSError:
        return {}
    if key == _cache["key"]:
        return _cache["days"]
    rows = []
    for p in files:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue     # a half-synced last line
    days: dict = {}
    for r in sorted(rows, key=lambda r: str(r.get("ts"))):
        if r.get("day") and r.get("id"):
            days.setdefault(r["day"], {})[str(r["id"])] = r.get("pts")
    _cache.update(key=key, days=days)
    return days


def for_day(day) -> dict:
    """{entry id: 分, or None for a tombstone}."""
    d = _dir()
    return {} if d is None else dict(_load(d).get(_iso(day), {}))


def overlay(local: dict, day) -> dict:
    """`local` {id: 分} with the shared records on top, tombstoned ids gone."""
    out = {str(k): v for k, v in (local or {}).items()}
    for k, v in for_day(day).items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out
