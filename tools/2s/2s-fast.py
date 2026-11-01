#!/usr/bin/env python3
"""2s-fast.py — copy a month's Neon sums into the 计分卡 2s,3s scorecard.

Source: Neon分v12.2.xlsx (open in Excel on IX), sheet 1分+1s. Monthly aggregates
live on the month's FIRST week row (col A == M.1), columns AQ..BA + BC.

Target: 计分卡  2s, 3s.xlsx (OneDrive vault-excel, open in Excel on IX like the
source; moved out of Dropbox 'Eclipse SSD' 2026-10-27), sheet '18-26 2s'. Month row is looked
up by col A == year, col B == month. Columns C..N skipping I (deprecated 5^1₦),
plus Y.

Header-verified mapping (2026-07-05):
  AQ sd-neon→C · AR neon→D(com0) · AS all-color→E(∀clr) · AT hcmc/day→F
  AU hcb/d→G(hcbc) · AV s897/d→H · AW 5^0g/d→J(0₦/d) · AX i9→K · AY x2→L
  AZ -2g→M · BA xk87/d→N · BC 分/d→Y

Values are pasted as literals (SET), so re-runs are idempotent refreshes.

Usage:
  python3 2s-fast.py <month> [--year YYYY] [--dry-run] [--raw]
  <month>: 4 | april | apr | 2026-04

--raw (2026-11-01, Todoist 66jC2ghQ28r6xHqc): compute C/D/E/J straight from
the month's raw 0分 rows (E 0₦t, F ∀₦t, Q 0g) instead of pasting the
1分+1s M.1 formulas, which point at the wrong rows/columns for several 2026
months (AQ15 reads E39:E67, AR28/AR32/AR41 count col F, AW36 divides a
5-week sum by 4, ...). Y (分/d) is also computed from 0分 D, but only used
when the M.1 formula cell is blank/#REF! (October: 1分+1s!C43 carries a
"+#REF!" tail), since the weekly C cells can hold JM's manual adjustments.
The formulas the raw path reproduces, as they SHOULD read:
  C sd 0₦  = COUNTIFS(E, ">600", E, "<1000") / days
  D com0   = COUNTIF(E, ">1") / days
  E ∀clr   = COUNTIF(F, ">0") / days
  J 0₦/d   = AVERAGE over weeks of AVERAGE(Q of that week)   [1分+1s F]
  Y 分/d   = AVERAGE over weeks of SUM(D of that week) / 7   [1分+1s C, BC]
where `days` = 7 × the month's 4-4-5 fiscal week count (lib/neon/weeks.py).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from neon.weeks import WEEK_QUOTA, first_sunday_of_year  # noqa: E402

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
SCORECARD = "计分卡  2s, 3s.xlsx"   # note: two spaces before 2s
# Path on IX (OneDrive-Personal), where the write runs; moved from Dropbox
# 'Eclipse SSD' 2026-10-27 so both workbooks follow the write-on-Ix rule.
SCORECARD_PATH = "/Users/mckay/OneDrive/vault-excel/" + SCORECARD
SHEET_2S = "18-26 2s"
NEON_WB = "Neon分v12.2.xlsx"
SHEET_1S = "1分+1s"

SRC_COLS = ["AQ", "AR", "AS", "AT", "AU", "AV", "AW", "AX", "AY", "AZ", "BA"]
DST_COLS = ["C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N"]  # I skipped
SRC_EXTRA, DST_EXTRA = "BC", "Y"
# C sd-neon, D com0, E ∀clr are day-fractions (0.0-1.0, e.g. 0.71), not 分:
# rounding them to int pastes 0/1 (2026-10-29: Oct C 0.714 -> 1, E 0.464 -> 0).
RATE_COLS = {"C", "D", "E"}

# --raw: scorecard columns computed from raw 0分 rows; Y only as a fallback.
RAW_COLS = ("C", "D", "E", "J")
SHEET_0FEN = "0分"
# 0分 columns read by --raw: B date, D Σ, E 0₦t, F ∀₦t, Q 0g (B..Q in one range).
FEN_FIRST_COL = "B"
FEN_LAST_COL = "Q"


def _col_idx(c: str) -> int:
    n = 0
    for ch in c:
        n = n * 26 + ord(ch) - 64
    return n


def fiscal_month(year: int, month: int) -> tuple[date, int]:
    """(first Sunday, week count) of label-month `month` (4-4-5 quota)."""
    start = first_sunday_of_year(year) + timedelta(weeks=sum(WEEK_QUOTA[:month - 1]))
    return start, WEEK_QUOTA[month - 1]


def fen_row(d: date) -> int:
    """0分 row of day `d`: row 2 is the year's first Sunday (week 1.1), one
    row per day. Matches 1分+1s F's own INDIRECT((ROW()-2)*7+2) and v173's
    anchors (4.1 → 93, 10.1 → 275)."""
    return 2 + (d - first_sunday_of_year(d.year)).days


def _num(v) -> float | None:
    """Excel COUNTIF/AVERAGE semantics: numbers count, blanks/text don't."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def parse_fen_date(v: str) -> date | None:
    """0分!B renders as 'Sunday, April 5, 2026 at 12:00:00\u202fAM'."""
    head = str(v).replace("\u202f", " ").split(" at ")[0].strip()
    for fmt in ("%A, %B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            pass
    return None


def compute_raw(days: list[dict], n_weeks: int) -> dict[str, float | None]:
    """C/D/E/J/Y from the month's 0分 days (dicts with keys D, E, F, Q; length
    7 × n_weeks, in date order; missing/future days may be blank)."""
    n_days = 7 * n_weeks
    es = [_num(d.get("E")) for d in days]
    fs = [_num(d.get("F")) for d in days]
    out: dict[str, float | None] = {
        "C": sum(1 for e in es if e is not None and 600 < e < 1000) / n_days,
        "D": sum(1 for e in es if e is not None and e > 1) / n_days,
        "E": sum(1 for f in fs if f is not None and f > 0) / n_days,
    }
    week_q, week_d = [], []
    for w in range(n_weeks):
        chunk = days[7 * w:7 * w + 7]
        q = [x for x in (_num(d.get("Q")) for d in chunk) if x is not None]
        dd = [x for x in (_num(d.get("D")) for d in chunk) if x is not None]
        if q:
            week_q.append(sum(q) / len(q))
        if dd:
            week_d.append(sum(dd))
    out["J"] = sum(week_q) / len(week_q) if week_q else None
    out["Y"] = sum(week_d) / len(week_d) / 7 if week_d else None
    return out


def read_fen_month(year: int, month: int) -> tuple[list[dict], int, int]:
    """Read-only: the month's 0分 rows via the excel-http daemon /read_range.
    Returns (days, n_weeks, first_row). Fails loudly on a date mismatch so a
    shifted sheet never feeds the scorecard."""
    from neon import excel as neon_excel
    start, n_weeks = fiscal_month(year, month)
    r0 = fen_row(start)
    r1 = r0 + 7 * n_weeks - 1
    out = neon_excel.read_range(SHEET_0FEN, f"{FEN_FIRST_COL}{r0}:{FEN_LAST_COL}{r1}")
    if not out.get("ok"):
        sys.exit(f"ERROR reading {SHEET_0FEN}!{r0}:{r1}: {out.get('error')}")
    base = _col_idx(FEN_FIRST_COL)
    days = []
    for i, row in enumerate(out["rows"]):
        want = start + timedelta(days=i)
        got = parse_fen_date(row[0]) if row else None
        if got != want:
            sys.exit(f"ERROR: {SHEET_0FEN} row {r0 + i} is {row[0] if row else '?'!r}, "
                     f"expected {want} — sheet layout moved, refusing --raw")
        days.append({c: row[_col_idx(c) - base] if _col_idx(c) - base < len(row) else ""
                     for c in ("D", "E", "F", "Q")})
    return days, n_weeks, r0


def fmt_raw(col: str, v: float | None) -> str:
    if v is None:
        return '""'
    return normalize_value(repr(v), rate=col in RATE_COLS)


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
    """osascript on IX: find the (year, month) row in 18-26 2s, SET the 12 cells.
    Opens the workbook from OneDrive if not already open."""
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
    r = subprocess.run([str(IX_OSA)], input=script, capture_output=True,
                       text=True, timeout=120)
    out = (r.stdout or "").strip()
    if r.returncode != 0 or out.startswith("ERROR"):
        sys.exit(f"ERROR writing scorecard on ix: {out or r.stderr.strip()}")
    return out


def normalize_value(v: str, rate: bool = False) -> str:
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
        if rate:
            return f"{round(float(v), 2):g}"
        return str(int(round(float(v))))
    except ValueError:
        return '"' + v.replace('"', '') + '"'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("month", nargs="?", default=None,
                    help="4 | april | 2026-04 (default: previous month)")
    ap.add_argument("--year", type=int, default=date.today().year)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--raw", action="store_true",
                    help="compute C/D/E/J (and Y if its formula is broken) "
                         "from raw 0分 rows instead of the 1分+1s M.1 formulas")
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
    norm = [normalize_value(v, rate=c in RATE_COLS)
            for c, v in zip(DST_COLS + [DST_EXTRA], vals)]
    dst = DST_COLS + [DST_EXTRA]
    notes = []
    if a.raw:
        days, n_weeks, r0 = read_fen_month(year, month)
        raw = compute_raw(days, n_weeks)
        formula = dict(zip(dst, norm))
        for c in RAW_COLS + (DST_EXTRA,):
            rv = fmt_raw(c, raw[c])
            if c == DST_EXTRA and formula[c] not in ('""',) and not formula[c].startswith('"'):
                continue  # Y formula is readable: keep it (may hold manual adjustments)
            if rv != formula[c]:
                notes.append(f"{c} {formula[c]}→{rv}")
            norm[dst.index(c)] = rv
        src_row = f"{src_row} + 0分!{r0}:{r0 + 7 * n_weeks - 1} raw"
    empty = sum(1 for v in norm if v == '""')
    if empty >= len(norm):
        sys.exit(f"ERROR: {SHEET_1S} row {src_row} ({month}.1) is empty — "
                 f"month {month} not aggregated yet")

    if a.dry_run:
        print(f"DRY RUN — {year}-{month:02d}: 1分+1s row {src_row} → {SHEET_2S}")
        for (c, v) in zip(DST_COLS + [DST_EXTRA], norm):
            print(f"  {c} ← {v}")
        if notes:
            print("  raw overrides (formula→raw): " + ", ".join(notes))
        return
    out = write_scorecard(year, month, norm, dry=False)
    disp = {c: v for c, v in zip(DST_COLS + [DST_EXTRA], norm)}
    print(f"2s → {year}-{month:02d}: 1分+1s!row{src_row} (AQ:BA+BC) → "
          f"{SHEET_2S} {out.replace('OK ', '')} (C:N skip I, +Y) · "
          f"分/d={disp[DST_EXTRA]}"
          + (f" · {empty} empty cells passed through" if empty else "")
          + (f" · raw: {', '.join(notes)}" if notes else ""))


if __name__ == "__main__":
    main()
