#!/usr/bin/env python3
"""media-tag-audit.py — did the Toggl media value tags (#-1 / #-2 / #-3) land in 0n?

Usage:
    media-tag-audit.py <start YYYY-MM-DD> <end YYYY-MM-DD> [--fix] [--json]

For each local day in the range, sums the minutes of Toggl entries carrying an
explicit value tag (shortcode-implied tags are skipped, same rule as
tag_credits.py) and compares them to the 0n minute columns the tags feed:
-1 → AV, -2 → AW, -3 → AX (AX = 新闻 N + 词汇 O + tagged extras, so the
tagged part is AX − N − O). Prints a per-day diff.

--fix appends the shortfall to the column through the same neon.excel path
tag_credits uses (src "1hcmc media-tag audit"). It never subtracts: a day where
the sheet has MORE than Toggl is flagged, not touched.

Why this exists (2026-09-27): tag credits fire in toggl_api.stop_timer, so an
entry closed any other way (trim_range, mobile retime, a backfilled span)
earns nothing; /1hcmc week 9.16-9.22 found 324 #-1 minutes uncredited.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
MCP = Path.home() / "i446-monorepo/mcp"
TAGS = ("-1", "-2", "-3")
COLS = {"-1": 48, "-2": 49, "-3": 50}   # 0n AV / AW / AX
N_COL, O_COL = 14, 15                   # 新闻, 词汇 — folded into AX by formula


def _toggl_key() -> str:
    try:
        d = json.load(open(os.path.expanduser("~/.claude.json")))
        return d["mcpServers"]["toggl_server"]["env"]["TOGGL_API_KEY"]
    except Exception:  # noqa: BLE001
        return ""


os.environ.setdefault("TOGGL_API_KEY", _toggl_key())
os.environ.setdefault("TOGGL_WORKSPACE_ID", "2092616")
sys.path.insert(0, str(MCP))
from toggl_server import toggl_api  # noqa: E402
from toggl_server import tag_credits  # noqa: E402

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
from neon import excel as neon_excel  # noqa: E402  excel-http daemon client (0n reads)


def toggl_minutes(start: dt.date, end: dt.date) -> dict[dt.date, dict[str, int]]:
    """{day: {tag: minutes}} for explicit value tags, local days in [start, end]."""
    entries = toggl_api.get_entries(start_date=(start - dt.timedelta(days=1)).isoformat(),
                                    end_date=(end + dt.timedelta(days=2)).isoformat()) or []
    out: dict[dt.date, dict[str, int]] = {}
    for e in entries:
        tags = [t for t in (e.get("tags") or []) if t in TAGS]
        if not tags:
            continue
        auto = tag_credits._auto_tags_for(e.get("description") or "")
        if auto is None:
            continue
        try:
            day = dt.datetime.fromisoformat(str(e["start"]).replace("Z", "+00:00")).astimezone(TZ).date()
        except (KeyError, ValueError):
            continue
        if not (start <= day <= end):
            continue
        mins = tag_credits.entry_minutes(e)
        for t in tags:
            if t in auto:
                continue
            out.setdefault(day, {}).setdefault(t, 0)
            out[day][t] += mins
    return out


# read_range returns cell VALUES as text; a real date cell comes back in
# AppleScript's long form ("Wednesday, November 25, 2026 at 12:00:00\u202fAM").
_LONG_DATE_RE = re.compile(r"^[A-Za-z]+, ([A-Za-z]+) (\d{1,2}), (\d{4}) at ")
_MONTH_NUM = {n: i for i, n in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
FIRST_ROW, LAST_ROW = 3, 500
DATE_COL = 3                            # 0n col C: the day


def _cell_date(text: str) -> dt.date | None:
    """A real date cell as read_range returns it, else None."""
    m = _LONG_DATE_RE.match(text or "")
    if not m or m.group(1) not in _MONTH_NUM:
        return None
    try:
        return dt.date(int(m.group(3)), _MONTH_NUM[m.group(1)], int(m.group(2)))
    except ValueError:
        return None


def _letter(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _read(rng: str) -> list:
    r = neon_excel.read_range("0n", rng)
    rows = r.get("rows") if r.get("ok") else None
    if not isinstance(rows, list):
        raise SystemExit(f"ERROR: 0n read failed: {r.get('error') or r}")
    return rows


def sheet_minutes(start: dt.date, end: dt.date) -> dict[dt.date, dict[str, float]]:
    """{day: {tag: minutes}} from 0n, through the excel-http daemon (no raw
    AppleScript, 2026-11-25). Rows are matched on month/day of col C, as
    before: 0n holds one year."""
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    dates = [_cell_date(str(c[0])) if c else None for c in _read(
        f"{_letter(DATE_COL)}{FIRST_ROW}:{_letter(DATE_COL)}{LAST_ROW}")]
    if not any(dates):
        # Dates that did not come back as dates would read as "nothing in the
        # sheet", and --fix would then append every tagged minute again.
        raise SystemExit("ERROR: 0n read failed: no date cells in column C")
    hits = []                           # (sheet row, day), in row order
    for k, cd in enumerate(dates):
        if cd is None:
            continue
        day = next((x for x in days if x.month == cd.month and x.day == cd.day), None)
        if day is not None:
            hits.append((FIRST_ROW + k, day))
    out: dict[dt.date, dict[str, float]] = {}
    if not hits:
        return out
    want = [COLS["-1"], COLS["-2"], COLS["-3"], N_COL, O_COL]
    lo_c, hi_c = min(want), max(want)
    lo_r, hi_r = min(r for r, _ in hits), max(r for r, _ in hits)
    # One block over the matched rows: at most 498 rows x 37 columns (N..AX),
    # inside the daemon's 20,000-cell limit.
    block = _read(f"{_letter(lo_c)}{lo_r}:{_letter(hi_c)}{hi_r}")

    def f(s):
        try:
            return float(s)             # blank / text → 0, like the old read
        except ValueError:
            return 0.0
    for row, day in hits:
        cells = block[row - lo_r] if row - lo_r < len(block) else []
        if len(cells) != hi_c - lo_c + 1:
            raise SystemExit(f"ERROR: 0n read failed: short row {row}")
        av, aw, ax, n, o = (f(str(cells[c - lo_c])) for c in want)
        # AX normally = N + O + tagged extras; a hand-entered AX below N + O
        # makes the derived tagged part negative — clamp, never "fix" it upward.
        out[day] = {"-1": av, "-2": aw, "-3": max(0.0, ax - n - o)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("start"); ap.add_argument("end")
    ap.add_argument("--fix", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    tog = toggl_minutes(start, end)
    sheet = sheet_minutes(start, end)
    rows, fixes = [], []
    for i in range((end - start).days + 1):
        day = start + dt.timedelta(days=i)
        for t in TAGS:
            tm = tog.get(day, {}).get(t, 0)
            sm = sheet.get(day, {}).get(t, 0.0)
            diff = tm - sm
            if tm == 0 and sm == 0:
                continue
            status = "ok" if abs(diff) < 0.5 else ("missing" if diff > 0 else "sheet>toggl")
            rows.append({"day": day.isoformat(), "tag": t, "toggl": tm, "sheet": sm, "status": status})
            if a.fix and diff > 0.5 and tm > 0:
                col = tag_credits._tag_col(t)
                tag_credits._append("0n", col, date=f"{day.month}/{day.day}", value=f"+{int(round(diff))}",
                                    src="1hcmc media-tag audit")
                fixes.append(f"{day.month}/{day.day} {t}: +{int(round(diff))} → 0n!{col}")
    if a.json:
        print(json.dumps({"rows": rows, "fixes": fixes}, ensure_ascii=False, indent=2)); return 0
    print(f"media tags {start} → {end}")
    print("day        tag  toggl  sheet  status")
    for r in rows:
        print(f"{r['day']}  {r['tag']:>3}  {r['toggl']:>5}  {r['sheet']:>5.0f}  {r['status']}")
    if not rows:
        print("(no tagged entries and nothing recorded)")
    for fx in fixes:
        print("fixed:", fx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
