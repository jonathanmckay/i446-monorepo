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
import importlib.util
import json
import os
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

_IX = importlib.util.spec_from_file_location("ix_osa", Path.home() / ".claude/skills/_lib/ix-osa.py")
_ix = importlib.util.module_from_spec(_IX); sys.modules["ix_osa"] = _ix; _IX.loader.exec_module(_ix)
ix_run = _ix.run


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


def sheet_minutes(start: dt.date, end: dt.date) -> dict[dt.date, dict[str, float]]:
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    keys = " or ".join(f"(m = {d.month} and dd = {d.day})" for d in days)
    script = f'''tell application "Microsoft Excel"
    set ws to sheet "0n" of workbook "Neon分v12.2.xlsx"
    set out to ""
    repeat with r from 3 to 500
        set cd to value of cell 3 of row r of ws
        if cd is not missing value then
            try
                set m to (month of (cd as date)) as integer
                set dd to day of (cd as date)
                if {keys} then
                    set ln to m & "/" & dd
                    repeat with c in {{{COLS["-1"]}, {COLS["-2"]}, {COLS["-3"]}, {N_COL}, {O_COL}}}
                        set v to value of cell c of row r of ws
                        if v is missing value then set v to 0
                        set ln to ln & (character id 9) & (v as text)
                    end repeat
                    set out to out & ln & linefeed
                end if
            end try
        end if
    end repeat
    return out
end tell'''
    res = ix_run(script, timeout=60.0)
    if res.returncode != 0:
        raise SystemExit(f"ERROR: 0n read failed: {res.stderr or res.stdout}")
    out: dict[dt.date, dict[str, float]] = {}
    for line in res.stdout.splitlines():
        p = line.split("\t")
        if len(p) < 6:
            continue
        m, d = (int(x) for x in p[0].split("/"))
        day = next((x for x in days if x.month == m and x.day == d), None)
        if day is None:
            continue
        def f(s):  # noqa: E306
            try:
                return float(s)
            except ValueError:
                return 0.0
        av, aw, ax, n, o = (f(x) for x in p[1:6])
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
