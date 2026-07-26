#!/usr/bin/env python3
"""4gneon-s.py — Quarterly Neon scorecard: colors, basic habits, 1n%, 2n%.

Answers four questions for a calendar quarter, reading the live workbook
from ix (never the Straylight OneDrive mirror, which desyncs — see
reference_onedrive_neon_stale_sync memory):

  1. Days with "all colors" (0分!F, ∀₦t — sourced from 0n!AG, a timestamp;
     nonzero = achieved that day)
  2. Days with basic habits logged (0分!E, 0₦t — sourced from 0n!AF;
     nonzero = at least something logged that day)
  3. Avg 1n weekly-habit efficiency (1n+!AN, averaged over the month-anchor
     rows — the ".1" week of each month — whose date falls in the quarter)
  4. 2n monthly-task efficiency (1n+ row 88 — a rolling 3-month window table
     keyed by month number in row 61; picks the column whose row-61 value
     equals the quarter's start month, e.g. month 4 for Q2)

Usage:
    python3 4gneon-s.py                # most recently COMPLETED quarter
    python3 4gneon-s.py 2026-Q2        # explicit quarter
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.utils import column_index_from_string

REMOTE_XLSX = "~/OneDrive/vault-excel/Neon分v12.2.xlsx"
IX_HOST = "ix"


def fetch_live_workbook() -> Path:
    """scp the live file from ix — the Straylight OneDrive mirror can be
    days stale (known desync issue), so never read it directly."""
    tmp = Path(tempfile.mkdtemp()) / "Neon分v12.2.xlsx"
    subprocess.run(
        ["scp", f"{IX_HOST}:{REMOTE_XLSX}", str(tmp)],
        check=True, capture_output=True, text=True,
    )
    return tmp


def resolve_quarter(arg: str | None) -> tuple[int, int, date, date]:
    """Return (year, quarter_number, start_date, end_date)."""
    if arg:
        m = re.match(r"^(\d{4})-Q([1-4])$", arg.strip(), re.IGNORECASE)
        if not m:
            raise SystemExit(f"Bad quarter arg {arg!r}, expected e.g. 2026-Q2")
        year, q = int(m.group(1)), int(m.group(2))
    else:
        today = date.today()
        cur_q = (today.month - 1) // 3 + 1
        year, q = today.year, cur_q - 1
        if q == 0:
            year, q = year - 1, 4
    start_month = (q - 1) * 3 + 1
    start = date(year, start_month, 1)
    end_month = start_month + 2
    end_year = year + (1 if end_month > 12 else 0)
    end_month = end_month - 12 if end_month > 12 else end_month
    # last day of end_month
    if end_month == 12:
        end = date(end_year, 12, 31)
    else:
        next_month = date(end_year, end_month + 1, 1)
        end = date(next_month.year, next_month.month, 1)
        from datetime import timedelta
        end = end - timedelta(days=1)
    return year, q, start, end


def load_sheet_rows(ws, min_row=1, max_row=None):
    return list(ws.iter_rows(min_row=min_row, max_row=max_row, values_only=True))


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    year, q, start, end = resolve_quarter(arg)
    q_start_month = (q - 1) * 3 + 1

    xlsx_path = fetch_live_workbook()
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)

    # --- 0分: daily colors (F) + basic habits (E), keyed by week label (A) + date (B) ---
    ws_fen = wb["0分"]
    fen_rows = load_sheet_rows(ws_fen, min_row=2, max_row=500)

    today = date.today()
    q_days = []
    for r in fen_rows:
        week, dt, e_val, f_val = r[0], r[1], r[4], r[5]
        if not isinstance(dt, date):
            continue
        d = dt.date() if hasattr(dt, "date") else dt
        if not (start <= d <= end):
            continue
        # The sheet has pre-filled future template rows (real date, blank
        # inputs) — SUM() over a blank range evaluates to 0, not None, so
        # checking Sigma doesn't catch them. Filter on the calendar instead.
        if d > today:
            continue
        q_days.append({"week": week, "date": d, "E": e_val, "F": f_val})

    total_days = len(q_days)
    calendar_days = (end - start).days + 1
    is_partial_quarter = total_days < calendar_days

    def is_positive(v):
        try:
            return float(v) > 0
        except (TypeError, ValueError):
            return False

    days_all_colors = sum(1 for r in q_days if is_positive(r["F"]))
    days_basic_habits = sum(1 for r in q_days if is_positive(r["E"]))

    # --- 1n+: weekly AN%, averaged over weeks whose date falls in the quarter ---
    q_weeks = sorted({round(r["week"], 1) for r in q_days if isinstance(r["week"], (int, float))})

    ws_1n = wb["1n+"]
    an_col = column_index_from_string("AN") - 1
    week_rows = load_sheet_rows(ws_1n, min_row=6, max_row=60)
    an_vals = []
    an_detail = []
    for r in week_rows:
        wk = r[1]  # column B = week label (column A is a constant tag, not the week)
        if not isinstance(wk, (int, float)):
            continue
        wk_r = round(wk, 1)
        if wk_r in q_weeks:
            an = r[an_col]
            if isinstance(an, (int, float)):
                an_vals.append(an)
                an_detail.append((wk_r, an))

    avg_1n_pct = (sum(an_vals) / len(an_vals) * 100) if an_vals else None

    # --- 1n+ row 88: pick the column whose row-61 value == quarter's start month ---
    # This table is keyed by month NUMBER only (1-12), with no year dimension —
    # it's rebuilt by hand each year. Guard against silently reusing a prior
    # year's column by checking 0n's row-1 year label (col C) matches the
    # requested year before trusting row 88 at all.
    ws_n = wb["0n"]
    sheet_year = load_sheet_rows(ws_n, min_row=1, max_row=1)[0][2]
    year_mismatch = isinstance(sheet_year, (int, float)) and int(sheet_year) != year

    row61 = load_sheet_rows(ws_1n, min_row=61, max_row=61)[0]
    row88 = load_sheet_rows(ws_1n, min_row=88, max_row=88)[0]
    col_2n = None
    if not year_mismatch:
        for idx, v in enumerate(row61):
            if isinstance(v, (int, float)) and int(v) == q_start_month:
                col_2n = idx
                break
    pct_2n = None
    if col_2n is not None:
        v = row88[col_2n]
        if isinstance(v, (int, float)):
            pct_2n = v * 100

    xlsx_path.unlink(missing_ok=True)
    xlsx_path.parent.rmdir()

    # --- Report ---
    print(f"4gneon-s — {year} Q{q} ({start.isoformat()} to {end.isoformat()}), {total_days}/{calendar_days} days elapsed")
    if is_partial_quarter:
        print(f"NOTE: quarter is incomplete (only {total_days} of {calendar_days} calendar days have real data) — percentages below are over elapsed days only")
    print()
    print(f"1. All colors:     {days_all_colors}/{total_days} days ({days_all_colors/total_days*100:.1f}%)" if total_days else "1. All colors:     no data")
    print(f"2. Basic habits:   {days_basic_habits}/{total_days} days ({days_basic_habits/total_days*100:.1f}%)" if total_days else "2. Basic habits:   no data")
    if avg_1n_pct is not None:
        detail = ", ".join(f"{wk}={v*100:.0f}%" for wk, v in an_detail)
        print(f"3. 1n efficiency:  {avg_1n_pct:.1f}% avg over {len(an_vals)} week(s) [{detail}]")
    else:
        print("3. 1n efficiency:  no AN data found for this quarter's weeks")
    if year_mismatch:
        print(f"4. 2n efficiency:  skipped — 1n+ row88 is a month-number table built for {int(sheet_year)}, not {year}; re-check manually once the sheet is extended")
    elif pct_2n is not None:
        print(f"4. 2n efficiency:  {pct_2n:.1f}% (1n+ row88, month-{q_start_month} column)")
    else:
        print(f"4. 2n efficiency:  no row-88 column found for month {q_start_month}")


if __name__ == "__main__":
    main()
