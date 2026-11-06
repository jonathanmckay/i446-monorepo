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

A task closed with no time entry leaves an open credit (entry-credits.<host>.jsonl)
until its card's cycle ends. The next entry of that name made after the close
takes the points, once: link() when a client fetches entries, claim() when done
is pressed on a row. Only fixed-value tasks get credits; a per-minute habit's
entry has minutes of its own to log.

The entry must have started on or before the close day (Dream v180, rule (a)):
a daily credit takes only an entry of its own day, a weekly one an entry from
its week's Sunday through the close day. A later entry of the same name in the
week is new work, e.g. week_fill's catch-up of last week, and logs on its own.
link() and claim() apply the same test (_fits).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

HOST = os.uname().nodename.split(".")[0].lower()
_cache: dict = {"key": None, "days": {}}
_credit_cache: dict = {"key": None, "credits": {}}
_ANNOT = re.compile(r"\(\d*\)|\[\d*G?\]|\{\d*\}|(?<!\S)[@#]\S+")
_DATE_SUFFIX = re.compile(r"\s+\d{1,2}[./]\d{1,2}\s*$")


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


# ── open credits: a closed task waiting for its time entry ──────────────────

def normalize(name: str) -> str:
    """"1 i447 10.31 (20) [25]" and "1 i447 @i447" -> "1 i447"."""
    s = _DATE_SUFFIX.sub("", _ANNOT.sub(" ", name or ""))
    return re.sub(r"[\s\-—–]+", " ", s).strip().lower()


def _append_credit(d: Path, line: dict) -> None:
    try:
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"entry-credits.{HOST}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), **line}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_credit(cid: str, names: list, pts: int, day, until, src: str = "",
                  weekly: bool | None = None) -> None:
    d = _dir()
    if d is not None:
        line = {"kind": "credit", "credit": cid, "src": src, "pts": int(pts),
                "keys": sorted({normalize(n) for n in names if normalize(n)}),
                "day": _iso(day), "until": _iso(until)}
        if weekly is not None:
            line["weekly"] = bool(weekly)
        _append_credit(d, line)


def _credits(d: Path) -> dict:
    """{credit id: {..., "epoch", "links": [(day, entry id)], "void": bool}}."""
    try:
        files = sorted(d.glob("entry-credits.*.jsonl"))
        key = (str(d), tuple((str(p), p.stat().st_mtime_ns) for p in files))
    except OSError:
        return {}
    if key == _credit_cache["key"]:
        return _credit_cache["credits"]
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
                continue
    out: dict = {}
    for r in sorted(rows, key=lambda r: r.get("ts") or 0):
        c = r.get("credit")
        if r.get("kind") == "credit":
            out.setdefault(c, {"links": [], "void": False}).update(
                r, epoch=r.get("ts") or 0)
        elif c in out and r.get("kind") == "link":
            out[c]["links"].append((r.get("day"), str(r.get("id"))))
        elif c in out and r.get("kind") == "void":
            out[c]["void"] = True
    _credit_cache.update(key=key, credits=out)
    return out


def _open_credits(d: Path, today: str) -> list:
    return sorted((c for c in _credits(d).values()
                   if not c["void"] and not c["links"] and today <= c.get("until", "")),
                  key=lambda c: c["epoch"])


def _take(d: Path, c: dict, day: str, entry_id) -> int:
    record(day, {str(entry_id): c["pts"]}, f"credit {c['credit']}")
    _append_credit(d, {"kind": "link", "credit": c["credit"], "id": str(entry_id), "day": day})
    c["links"].append((day, str(entry_id)))
    return c["pts"]


def _epoch(at) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _first_day(c: dict) -> str:
    """Earliest day an entry may start on to take credit `c`: the week's
    Sunday for a weekly card (until is its Saturday), else the close day.
    Lines written before the flag existed infer weekly from until > day."""
    weekly = c.get("weekly")
    if weekly is None:
        weekly = c.get("until", "") > c.get("day", "")
    if not weekly:
        return c.get("day", "")
    try:
        return (dt.date.fromisoformat(c["until"]) - dt.timedelta(days=6)).isoformat()
    except (KeyError, ValueError):
        return c.get("day", "")


def _fits(c: dict, day: str, at: float | None) -> bool:
    """The entry started on `day` (and, when known, was made at epoch `at`)
    may take credit `c`: made after the close, started on or before the close
    day. A later entry of the name is new work, not this card's time."""
    if at is not None and at < c["epoch"]:
        return False
    return _first_day(c) <= day <= c.get("day", "")


def link(entries: list, today=None) -> dict:
    """Give open credits to matching entries made after the close and started
    on or before the close day (_fits). `entries` are client rows: id, desc,
    at, running, and start or start_dt. Earliest start wins, so every host
    picks the same one. -> {entry id: 分}."""
    d = _dir()
    if d is None:
        return {}
    credits = _open_credits(d, _iso(today or dt.date.today()))
    if not credits:
        return {}
    linked: dict = {}
    for c in credits:
        pool = []
        for e in entries:
            start = e.get("start_dt") or e.get("start")
            at = _epoch(e.get("at"))
            if e.get("running") or not start or at is None:
                continue
            if normalize(e.get("desc", "")) not in c["keys"] or str(e.get("id")) in linked:
                continue
            day = start.date().isoformat()
            if _fits(c, day, at) and str(e.get("id")) not in for_day(day):
                pool.append((start, str(e.get("id")), day))
        if pool:
            start, eid, day = min(pool)
            linked[eid] = _take(d, c, day, eid)
    return linked


def claim(entry_id, desc: str, day, at=None) -> int | None:
    """Done pressed on a row that started on `day`: an open credit of its name
    is this row's when link() would have given it (_fits), so the press must
    not credit again. `at` is the entry's Toggl "at" when the client has it;
    without it only the day is checked. -> 分 taken, or None."""
    d = _dir()
    if d is None:
        return None
    key, day_iso = normalize(desc), _iso(day)
    made = _epoch(at) if at else None
    for c in _open_credits(d, day_iso):
        if key in c["keys"] and _fits(c, day_iso, made):
            return _take(d, c, day_iso, entry_id)
    return None


def void_credit(cid: str) -> None:
    """Undo: the credit is gone, and so are the points on any entry it reached."""
    d = _dir()
    if d is None:
        return
    for day, eid in (_credits(d).get(cid) or {}).get("links", []):
        record(day, {eid: None}, "undo")
    _append_credit(d, {"kind": "void", "credit": cid})
