"""JM Dash data layer: points and time as one long-format event table.

Every row is {metric, ts, day, project, source, via, value}:
  metric   "points" (Neon 0分 point columns) or "time" (Toggl minutes)
  ts       local ISO timestamp, or None when only the day is known
  day      YYYY-MM-DD
  project  0分 column label (i9, m5, 个, ...) for points; Toggl code for time
  source   cli | 1p-app | 3p-app | watch | unknown   (see lib/jmsource.py)
  via      the specific tool (did-fast, excel, ...), or "" when unknown

query() then filters, groups by project or source, and buckets by
day / week / month / 2-hour block, so one chart can slice either metric by
either dimension. Pure functions over plain inputs, so the attribution rules
are testable without Excel, Toggl, or the ledger on disk.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))

LEDGER_DIR = os.path.expanduser("~/vault/g245/neon-ledger")
POINTS_SHEET = "0分"
UNATTRIBUTED_VIA = "unattributed"

BRANCHES = [("卯", 4), ("辰", 6), ("巳", 8), ("午", 10), ("未", 12),
            ("申", 14), ("酉", 16), ("戌", 18), ("亥", 20)]

_NUM_APPEND = re.compile(r"^\s*([+-])\s*(\d+(?:\.\d+)?)\s*$")


def _f(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == "":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def _row(metric, ts, day, project, source, via, value, count=1):
    """`count` is 1 for a real event, 0 for synthetic remainder rows."""
    return {"metric": metric, "ts": ts, "day": day, "project": project,
            "source": source, "via": via, "value": value, "count": count}


def source_label(r: dict) -> str:
    """Source as shown and filtered: 3p apps split by tool ("3p-app · excel")."""
    if r["source"] == "3p-app":
        return f"3p-app · {r.get('via') or '?'}"
    return r["source"]


_DIM = {"project": lambda r: r["project"], "source": source_label,
        "tool": lambda r: r.get("via") or "?"}


# ── Points: Neon write ledger ─────────────────────────────────────────────────

def load_ledger(first_day: date, last_day: date, ledger_dir: str = LEDGER_DIR) -> list[dict]:
    """Ledger entries for the months covering [first_day - 1 month, last_day],
    in file (= time) order. The extra leading month seeds each cell's previous
    value so the first in-window delta is right."""
    months = []
    cur = date(first_day.year, first_day.month, 1) - timedelta(days=1)
    cur = date(cur.year, cur.month, 1)
    while cur <= last_day:
        months.append(cur.strftime("%Y-%m"))
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    out = []
    for m in months:
        try:
            with open(os.path.join(ledger_dir, m + ".jsonl"), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # torn tail from a live append
        except FileNotFoundError:
            continue
    return out


def _entry_day(e: dict, row_days: dict) -> str:
    """The 0分 row's own date (the day the points count toward), not the
    write time: a 00:30 write to yesterday's row is yesterday's points."""
    ts = e.get("ts") or ""
    year = int(ts[:4]) if ts[:4].isdigit() else date.today().year
    d = e.get("date")
    if d and re.fullmatch(r"\d{1,2}/\d{1,2}", str(d)):
        m, dd = (int(x) for x in str(d).split("/"))
        try:
            return date(year, m, dd).isoformat()
        except ValueError:
            pass
    if e.get("row") in row_days:
        return row_days[e["row"]]
    return ts[:10]


def points_events(entries: list[dict], col_project: dict[str, str]) -> list[dict]:
    """Ledger entries → points rows, one per attributable delta.

    A cell is (sheet, col, row): ritual -1n writes are row-addressed (date
    null) and did-fast writes date-addressed, but both carry the row.

      baseline        no points; just sets the cell's starting value
      ack             no points; re-baselines after a blessed manual edit
      structural      no points (row/column moves), skipped
      excel-edit      Excel's own audit of a manual edit (excel-http 1.5):
                      after_value - before_value → 3p-app / excel. A late
                      report (after null, observed_after set) still counts,
                      but doesn't become the cell's latest value.
      reconcile /     Excel edit the pipeline never wrote over:
        excel-edit      after - before → 3p-app / excel
      write / append  pipeline delta after - before → entry source (or cli).
                      With before_value, a gap between it and the cell's
                      previous value is a separate outside (Excel) edit.
                      Without before_value (pre-2026-10-05 entries): delta
                      from the previous value, or the appended literal, or
                      the whole value for a first write.
    """
    row_days = {}
    for e in entries:
        if e.get("sheet") == POINTS_SHEET and e.get("date") and e.get("row"):
            row_days.setdefault(e["row"], _entry_day(e, {}))

    prev: dict[tuple, float] = {}
    rows = []
    for e in entries:
        if e.get("sheet") != POINTS_SHEET:
            continue
        project = col_project.get(e.get("col"))
        if project is None:
            continue
        cell = (e.get("col"), e.get("row") or e.get("date"))
        kind = e.get("kind")
        if kind == "structural":
            continue
        after = _f(e.get("after_value"))
        if after is None and kind == "excel-edit":
            after = _f(e.get("observed_after"))
        if after is None:
            continue
        ts = e.get("ts")
        day = _entry_day(e, row_days)
        before_known = prev.get(cell)

        if kind in ("baseline", "ack"):
            prev[cell] = after
            continue
        if kind in ("reconcile", "excel-edit"):
            base = _f(e.get("before_value"))
            if base is None:
                base = before_known or 0.0
            if after - base:
                rows.append(_row("points", ts, day, project, "3p-app", "excel", after - base))
            late = kind == "excel-edit" and e.get("after") is None and e.get("observed_after") is not None
            if not late:
                prev[cell] = after
            continue
        if kind not in ("write", "append"):
            continue

        src = e.get("source") or "cli"
        via = e.get("via") or (str(e.get("src") or "").split(" ")[0])
        bv = _f(e.get("before_value")) if "before_value" in e else None
        if bv is not None:
            if before_known is not None and abs(bv - before_known) > 1e-9:
                rows.append(_row("points", ts, day, project, "3p-app", "excel", bv - before_known))
            delta = after - bv
        elif before_known is not None:
            delta = after - before_known
        else:
            m = _NUM_APPEND.match(str(e.get("value") or "")) if kind == "append" else None
            delta = (float(m.group(2)) * (-1 if m.group(1) == "-" else 1)) if m else after
        if delta:
            rows.append(_row("points", ts, day, project, src, via, delta))
        prev[cell] = after
    return rows


def unattributed_points(rows: list[dict], cache: dict, labels: list[str],
                        days: list[str]) -> list[dict]:
    """Cache total minus ledger-attributed points, per day and project, as
    source `unknown`. Formula-driven credits (0n habit refs, hcbi refs) move
    a cell's value without any ledger write, so without this remainder the
    sliced chart wouldn't add up to the existing Points chart."""
    have = defaultdict(float)
    for r in rows:
        have[(r["day"], r["project"])] += r["value"]
    out = []
    for d in days:
        day_data = cache.get(d) or {}
        for lab in labels:
            total = day_data.get(lab)
            if not isinstance(total, (int, float)):
                continue
            gap = round(float(total) - have[(d, lab)], 6)
            if gap:
                out.append(_row("points", None, d, lab, "unknown", UNATTRIBUTED_VIA, gap, count=0))
    return out


# ── Time: Toggl ───────────────────────────────────────────────────────────────

def time_events(entries: list[dict], id_source: dict[str, dict], cutover: str | None,
                project_of, tz) -> list[dict]:
    """Toggl entries → time rows (minutes). Source: the jmsource log row for
    the entry id if any (creator wins); else 3p-app when the entry starts at
    or after the time cutover; else unknown."""
    cut = datetime.fromisoformat(cutover) if cutover else None
    rows = []
    for e in entries:
        dur = e.get("duration") or 0
        if dur <= 0 or not e.get("start"):
            continue
        try:
            start = datetime.fromisoformat(str(e["start"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        local = start.astimezone(tz)
        logged = id_source.get(str(e.get("id")))
        if logged:
            source, via = logged.get("source") or "cli", logged.get("via") or ""
        elif cut is not None and start >= cut:
            source, via = "3p-app", "toggl"
        else:
            source, via = "unknown", ""
        rows.append(_row("time", local.isoformat(timespec="seconds"), local.date().isoformat(),
                         project_of(e.get("project_id")), source, via, dur / 60.0))
    return rows


def time_events_from_daily_cache(daily: dict, days: list[str]) -> list[dict]:
    """Older days from the Reports-v3 daily cache: minutes per project, no
    entry ids, so source is unknown and there is no time of day."""
    rows = []
    for d in days:
        for proj, minutes in (daily.get(d) or {}).items():
            if minutes:
                rows.append(_row("time", None, d, proj, "unknown", "", float(minutes), count=0))
    return rows


def jmsource_index(metric: str) -> dict[str, dict]:
    """{ext_id: row} from lib/jmsource's log; first write wins."""
    try:
        import jmsource
        rows = jmsource.load()
    except Exception:
        return {}
    out = {}
    for r in rows:
        if r.get("metric") == metric and r.get("ok", True):
            out.setdefault(str(r.get("ext_id")), r)
    return out


def time_cutover() -> str | None:
    try:
        import jmsource
        return (jmsource.CUTOVER or {}).get("time")
    except Exception:
        return None


# ── Query ─────────────────────────────────────────────────────────────────────

def _week_start(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)  # Sunday-start, like /1s


def _block_label(ts: str | None) -> str | None:
    if not ts:
        return None
    t = datetime.fromisoformat(ts)
    for name, h in BRANCHES:
        if h <= t.hour < h + 2:
            return f"{t.date().isoformat()} {name}"
    return None  # 22:00-04:00 sleep gap


def buckets(start: date, end: date, grain: str) -> list[str]:
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if grain == "day":
        return [d.isoformat() for d in days]
    if grain == "week":
        return sorted({_week_start(d).isoformat() for d in days})
    if grain == "month":
        return sorted({d.replace(day=1).isoformat() for d in days})
    if grain == "block":
        return [f"{d.isoformat()} {name}" for d in days for name, _ in BRANCHES]
    raise ValueError(f"unknown grain {grain!r}")


def bucket_of(row: dict, grain: str) -> str | None:
    d = date.fromisoformat(row["day"])
    if grain == "day":
        return row["day"]
    if grain == "week":
        return _week_start(d).isoformat()
    if grain == "month":
        return d.replace(day=1).isoformat()
    if grain == "block":
        return _block_label(row["ts"])
    raise ValueError(f"unknown grain {grain!r}")


def query(rows: list[dict], metric: str, start: date, end: date, grain: str = "day",
          filters: dict | None = None, group_by: str = "project",
          measure: str = "sum") -> dict:
    """Filter `rows` to one metric, the [start, end] day range, and any
    {project|source|tool: [...]} filters (source matches source_label);
    group by project, source, or tool; bucket by grain; measure "sum" (Σ
    value) or "count" (events; synthetic remainder rows count 0).
    → {labels, series: {name: [values]}, totals: {name: v},
    dropped: measure excluded at block grain because it has no time of day}."""
    if group_by not in _DIM:
        raise ValueError(f"unknown group_by {group_by!r}")
    if measure not in ("sum", "count"):
        raise ValueError(f"unknown measure {measure!r}")
    field = "value" if measure == "sum" else "count"
    filters = {k: set(v) for k, v in (filters or {}).items() if v}
    labels = buckets(start, end, grain)
    pos = {b: i for i, b in enumerate(labels)}
    lo, hi = start.isoformat(), end.isoformat()
    series: dict[str, list[float]] = {}
    dropped = 0.0
    for r in rows:
        if r["metric"] != metric or not (lo <= r["day"] <= hi):
            continue
        if any(_DIM[k](r) not in allowed for k, allowed in filters.items()):
            continue
        v = r.get(field, 1) if field == "count" else r["value"]
        b = bucket_of(r, grain)
        if b is None or b not in pos:
            dropped += v
            continue
        key = _DIM[group_by](r)
        if field == "count" and v == 0 and key not in series:
            continue  # a group made only of synthetic rows has no events
        s = series.setdefault(key, [0.0] * len(labels))
        s[pos[b]] += v
    series = {k: [round(x, 2) for x in v] for k, v in series.items()}
    totals = {k: round(sum(v), 2) for k, v in series.items()}
    order = sorted(series, key=lambda k: -abs(totals[k]))
    return {"labels": labels, "series": {k: series[k] for k in order},
            "totals": {k: totals[k] for k in order}, "dropped": round(dropped, 2)}


def dimension_values(rows: list[dict], metric: str) -> dict[str, list[str]]:
    """Distinct values of each dimension for a metric (filter menus)."""
    out = {k: set() for k in _DIM}
    for r in rows:
        if r["metric"] == metric:
            for k, fn in _DIM.items():
                out[k].add(fn(r))
    return {k: sorted(v) for k, v in out.items()}
