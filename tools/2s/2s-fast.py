#!/usr/bin/env python3
"""2s-fast.py — copy a month's Neon sums into the 计分卡 2s,3s scorecard.

Source: Neon分v12.2.xlsx (open in Excel on IX), sheet 1分+1s. Monthly aggregates
live on the month's FIRST week row (col A == M.1), columns AQ..BA + BC.

Target: 计分卡  2s, 3s.xlsx (Dropbox 'Eclipse SSD', open in LOCAL Excel on
Straylight — Dropbox is not synced to Ix), sheet '18-26 2s'. Month row is looked
up by col A == year, col B == month. Columns C..N skipping I (deprecated 5^1₦),
plus Y.

Header-verified mapping (2026-07-05):
  AQ sd-neon→C · AR neon→D(com0) · AS all-color→E(∀clr) · AT hcmc/day→F
  AU hcb/d→G(hcbc) · AV s897/d→H · AW 5^0g/d→J(0₦/d) · AX i9→K · AY x2→L
  AZ -2g→M · BA xk87/d→N · BC 分/d→Y

Values are pasted as literals (SET), so re-runs are idempotent refreshes.

Usage:
  python3 2s-fast.py <month> [--year YYYY] [--dry-run]
  <month>: 4 | april | apr | 2026-04
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
SCORECARD = "计分卡  2s, 3s.xlsx"   # note: two spaces before 2s
SCORECARD_PATH = Path.home() / "Library/CloudStorage/Dropbox/Eclipse SSD" / SCORECARD
SHEET_2S = "18-26 2s"
NEON_WB = "Neon分v12.2.xlsx"
SHEET_1S = "1分+1s"

SRC_COLS = ["AQ", "AR", "AS", "AT", "AU", "AV", "AW", "AX", "AY", "AZ", "BA"]
DST_COLS = ["C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N"]  # I skipped
SRC_EXTRA, DST_EXTRA = "BC", "Y"

MONTHS = {m.lower(): i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def parse_month(s: str, default_year: int) -> tuple[int, int]:
    s = s.strip().lower()
    m = re.match(r"^(\d{4})[-./](\d{1,2})$", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    if s.isdigit() and 1 <= int(s) <= 12:
        return default_year, int(s)
    for name, num in MONTHS.items():
        if name.startswith(s):
            return default_year, num
    sys.exit(f"ERROR: cannot parse month {s!r}")


def read_neon_month(month: int) -> list:
    """One osascript on IX: find the M.1 row in 1分+1s, return its source values
    tab-separated (AQ..BA + BC)."""
    cells = SRC_COLS + [SRC_EXTRA]
    # NB: AppleScript's `tab` constant arrives as the literal text "tab" through
    # the ix-osa ssh path — use an explicit delimiter instead.
    reads = ' & "|~|" & '.join(
        f'(get value of range ("{c}" & theRow) of theSheet) as text' for c in cells)
    script = f'''tell application "Microsoft Excel"
    set theSheet to sheet "{SHEET_1S}" of workbook "{NEON_WB}"
    set theRow to 0
    repeat with r from 2 to 80
        try
            set a to value of range ("A" & r) of theSheet
            if a is not missing value and a is not "" then
                if (a as real) > {month}.05 and (a as real) < {month}.15 then
                    set theRow to r
                    exit repeat
                end if
            end if
        end try
    end repeat
    if theRow = 0 then return "ERROR: no {month}.1 row in {SHEET_1S}"
    return (theRow as text) & "|~|" & {reads}
end tell'''
    r = subprocess.run([str(IX_OSA)], input=script, capture_output=True,
                       text=True, timeout=60)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or out.startswith("ERROR"):
        sys.exit(f"ERROR reading Neon on ix: {out or r.stderr.strip()}")
    return out.split("|~|")


def write_scorecard(year: int, month: int, values: list[str], dry: bool) -> str:
    """Local osascript: find the (year, month) row in 18-26 2s, SET the 12 cells.
    Opens the workbook from Dropbox if not already open."""
    pairs = list(zip(DST_COLS + [DST_EXTRA], values))
    sets = "\n        ".join(
        f'set value of range ("{c}" & theRow) of theSheet to {v}'
        for c, v in pairs)
    script = f'''tell application "Microsoft Excel"
    set wbNames to (name of every workbook)
    if wbNames does not contain "{SCORECARD}" then open POSIX file "{SCORECARD_PATH}"
    set theSheet to sheet "{SHEET_2S}" of workbook "{SCORECARD}"
    set theRow to 0
    repeat with r from 2 to 500
        try
            if (value of range ("A" & r) of theSheet) = {year} and (value of range ("B" & r) of theSheet) = {month} then
                set theRow to r
                exit repeat
            end if
        end try
    end repeat
    if theRow = 0 then return "ERROR: no {year}-{month} row in {SHEET_2S}"
    {sets}
    save workbook "{SCORECARD}"
    return "OK row=" & theRow
end tell'''
    r = subprocess.run(["osascript", "-"], input=script, capture_output=True,
                       text=True, timeout=60)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or out.startswith("ERROR"):
        sys.exit(f"ERROR writing scorecard: {out or r.stderr.strip()}")
    return out


def normalize_value(v: str) -> str:
    """Excel cell text -> the literal to SET in the scorecard. 分 (and 分-
    derived rates like 分/d) are measured as integers everywhere else in this
    system (janus.py, the 0分 sheet); this pasted str(float(v)) straight
    through, so a value like 306 wrote as "306.0" and a genuinely fractional
    one (BC 分/d is a monthly average; some raw totals are themselves
    fractional — variable-task points divide by 7, e.g. 323.714285714286
    live in 0分) wrote its full repr (2026-07-14: "these values are adding
    decimals"). Round once, same as every other 分 render site."""
    v = v.strip()
    if v in ("missing value", ""):
        return '""'
    try:
        return str(int(round(float(v))))
    except ValueError:
        return '"' + v.replace('"', '') + '"'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("month", nargs="?", default=None,
                    help="4 | april | 2026-04 (default: previous month)")
    ap.add_argument("--year", type=int, default=date.today().year)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.month is None:
        # /2s is a month-end review — default to the month that just ended.
        first = date.today().replace(day=1)
        prev = first.replace(year=first.year - 1, month=12) if first.month == 1 \
            else first.replace(month=first.month - 1)
        year, month = prev.year, prev.month
    else:
        year, month = parse_month(a.month, a.year)

    raw = read_neon_month(month)
    src_row, vals = raw[0], raw[1:]
    if len(vals) != 12:
        sys.exit(f"ERROR: expected 12 values, got {len(vals)}: {vals}")
    # Normalize: AppleScript renders missing as "missing value"
    norm = [normalize_value(v) for v in vals]
    empty = sum(1 for v in norm if v == '""')
    if empty >= len(norm):
        sys.exit(f"ERROR: {SHEET_1S} row {src_row} ({month}.1) is empty — "
                 f"month {month} not aggregated yet")

    if a.dry_run:
        print(f"DRY RUN — {year}-{month:02d}: 1分+1s row {src_row} → {SHEET_2S}")
        for (c, v) in zip(DST_COLS + [DST_EXTRA], norm):
            print(f"  {c} ← {v}")
        return
    out = write_scorecard(year, month, norm, dry=False)
    disp = {c: v for c, v in zip(DST_COLS + [DST_EXTRA], norm)}
    print(f"2s → {year}-{month:02d}: 1分+1s!row{src_row} (AQ:BA+BC) → "
          f"{SHEET_2S} {out.replace('OK ', '')} (C:N skip I, +Y) · "
          f"分/d={disp[DST_EXTRA]}"
          + (f" · {empty} empty cells passed through" if empty else ""))


if __name__ == "__main__":
    main()
