#!/usr/bin/env python3
"""Resolve the last completed Sun-Sat week and its Neon 'i9'-sheet row/label
for /1i9.

Weeks run **Sunday through Saturday** (matches /1s, NOT /1s897's Wed-Tue
convention). Uses the same fiscal-week anchor and 4-4-5 quarterly split as
/1s897's week_calc.py (Sunday 2026-01-04 = fiscal week 1) -- duplicated
rather than imported so this skill stays self-contained, matching every
other per-skill week_calc.py in this repo. If the anchor or split ever
changes, update both copies.

Only 2026 has a confirmed anchor; other years raise rather than guess.

The Neon 'i9' sheet is pre-populated for the whole year: row 3 = fiscal
week 1 ('1.1'), incrementing one row per fiscal week (row = fiscal_week_num
+ 2). This script computes that row directly from the date instead of
re-deriving the M.W label first -- the label it prints is informational;
ALWAYS verify it against the sheet's own column A value before writing
(see SKILL.md Step 4).

Prints one line: week_start<TAB>week_end<TAB>row<TAB>label
  week_start / week_end -- ISO dates (Sunday, Saturday)
  row                   -- 1-indexed row in the 'i9' sheet
  label                 -- expected M.W label at that row (e.g. '9.2')
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

_FISCAL_YEAR_ANCHORS = {2026: date(2026, 1, 4)}


def _anchor(year: int) -> date:
    try:
        return _FISCAL_YEAR_ANCHORS[year]
    except KeyError:
        raise ValueError(
            "no confirmed fiscal-week-1 anchor for %d (only %s so far)"
            % (year, sorted(_FISCAL_YEAR_ANCHORS))
        )


def last_completed_week(today: date | None = None) -> tuple[date, date]:
    """(week_start Sunday, week_end Saturday) of the most recently completed
    Sun-Sat week -- never a week that includes today, unless today is
    Saturday itself (the last day of the week), matching /1s's convention."""
    today = today or date.today()
    days_since_sat = (today.weekday() - 5) % 7  # Mon=0..Sun=6; Sat=5
    if days_since_sat == 0:
        days_since_sat = 7
    week_end = today - timedelta(days=days_since_sat)
    return week_end - timedelta(days=6), week_end


def fiscal_week_num(sunday: date) -> int:
    anchor = _anchor(sunday.year)
    if sunday < anchor:
        raise ValueError("%s precedes fiscal-week-1 anchor %s" % (sunday, anchor))
    return (sunday - anchor).days // 7 + 1


def label_for_fiscal_week(n: int) -> str:
    """Inverse of /1s897's sunday_for_week_label: fiscal_week_num -> 'M.W'."""
    quarter, rem = divmod(n - 1, 13)  # rem 0..12 within the quarter
    if rem < 4:
        month_in_quarter, week = 0, rem + 1
    elif rem < 8:
        month_in_quarter, week = 1, rem - 4 + 1
    else:
        month_in_quarter, week = 2, rem - 8 + 1
    month = quarter * 3 + month_in_quarter + 1
    return "%d.%d" % (month, week)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("today", nargs="?", default=None,
                     help="ISO date to treat as 'today' (default: real today)")
    args = ap.parse_args()
    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
        week_start, week_end = last_completed_week(today)
        n = fiscal_week_num(week_start)
    except ValueError as e:
        print("ERR: %s" % e, file=sys.stderr)
        sys.exit(1)
    row = n + 2
    label = label_for_fiscal_week(n)
    print("%s\t%s\t%d\t%s" % (week_start.isoformat(), week_end.isoformat(), row, label))


if __name__ == "__main__":
    main()
