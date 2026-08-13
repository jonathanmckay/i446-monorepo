#!/usr/bin/env python3
"""Resolve the target Wed-Tue week for /1s897.

No arg      -> the most recent COMPLETE week (week_end <= today).
'M.W' arg   -> the Wed-Tue week containing that label's Sunday. 'M.W' is the
               CANONICAL fiscal-week label (Sunday-keyed), NOT a calendar-
               month-local "which Sunday falls in month M" count (that
               approximation -- still used by /did's 1n+ Step 1n -- silently
               diverges at every quarter boundary; see the 2026-08-13 bug
               below). Fiscal week 1 of a year starts on the Sunday on/after
               Jan 1. Weeks run consecutively; every 13-week quarter splits
               into fiscal months of 4, 4, then 5 weeks (the 3rd month of
               each quarter absorbs the extra week so 12*4=48 weeks doesn't
               fall short of the ~52/year total). M = which fiscal month
               (1-12) the week falls in, W = which week within that fiscal
               month (1-4, or 1-5 for a quarter's 3rd month).
ISO date arg -> the Wed-Tue week containing that date.

Prints one line: <week_start ISO>\\t<week_end ISO>

Bug (2026-08-13): the original implementation found "the Sunday whose day-of-
month falls in the Nth 7-day bucket of calendar month M" -- e.g. for '6.1' it
found the first Sunday that falls inside June (6/7) and derived Wed 6/3-Tue
6/9. JM's actual system doesn't reset week-of-month at calendar month
boundaries; '6.1' is fiscal week 22 (Sunday 5/31), which shifts to Wed
5/27-Tue 6/2. The two systems agree most of the time (both give '5.4' ->
Sunday 5/24) and silently disagree right at 4/4/5 quarter-boundary weeks --
exactly where it's least visible. Confirmed against JM's own correction of
both '5.4' and '6.1' during the 1s897 backfill that surfaced this.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta

_WEEK_LABEL_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})$")

# Fiscal week 1 of 2026 starts Sunday 2026-01-04 (the Sunday on/after Jan 1,
# per JM 2026-08-13). No other year's anchor is confirmed yet -- rather than
# guess a rule (e.g. "always the Sunday on/after Jan 1") and risk silently
# mislabeling a different year, only 2026 is wired up; other years raise.
_FISCAL_YEAR_ANCHORS = {2026: date(2026, 1, 4)}


def _fiscal_year_anchor(year: int) -> date:
    try:
        return _FISCAL_YEAR_ANCHORS[year]
    except KeyError:
        raise ValueError(
            "no confirmed fiscal-week-1 anchor for %d (only %s so far -- "
            "confirm the Sunday on/after Jan 1 rule holds and add it to "
            "_FISCAL_YEAR_ANCHORS before using M.W labels for this year)"
            % (year, sorted(_FISCAL_YEAR_ANCHORS))
        )


def sunday_for_week_label(label: str, year: int | None = None) -> date:
    """'M.W' -> that fiscal week's Sunday (4-4-5 quarterly split, see module
    docstring). Raises ValueError if the label doesn't parse, M isn't 1-12,
    or W is out of range for that fiscal month (4, or 5 for a quarter's 3rd
    month)."""
    m = _WEEK_LABEL_RE.match(label)
    if not m:
        raise ValueError("not a week label: %r" % label)
    month, week = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        raise ValueError("month out of range 1-12: %r" % label)
    year = year or date.today().year
    anchor = _fiscal_year_anchor(year)
    quarter, month_in_quarter = divmod(month - 1, 3)  # 0-indexed
    weeks_in_month = 5 if month_in_quarter == 2 else 4
    if not (1 <= week <= weeks_in_month):
        raise ValueError(
            "no week %s (fiscal month %d has %d weeks)" % (label, month, weeks_in_month)
        )
    weeks_before_month_in_quarter = [0, 4, 8][month_in_quarter]  # 4,4,5-week months
    fiscal_week_num = quarter * 13 + weeks_before_month_in_quarter + week  # 1-indexed
    return anchor + timedelta(days=(fiscal_week_num - 1) * 7)


def week_range(arg: str | None, today: date | None = None,
                year: int | None = None) -> tuple[date, date]:
    """Wed-Tue (week_start, week_end) for `arg`. See module docstring."""
    today = today or date.today()
    if arg and _WEEK_LABEL_RE.match(arg):
        anchor = sunday_for_week_label(arg, year)
    elif arg:
        anchor = date.fromisoformat(arg)
    else:
        # Tuesday weekday() == 1. days_back is 0 on Tue, 1 on Wed, ... 6 on Mon.
        days_back = (today.weekday() - 1) % 7
        week_end = today - timedelta(days=days_back)
        return week_end - timedelta(days=6), week_end
    # Wed weekday() == 2. days_since_wed is 0 on Wed ... 6 on Tue.
    days_since_wed = (anchor.weekday() - 2) % 7
    week_start = anchor - timedelta(days=days_since_wed)
    return week_start, week_start + timedelta(days=6)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("arg", nargs="?", default=None,
                     help="'M.W' week label (e.g. 7.4) or an ISO date "
                          "(YYYY-MM-DD) in the target week; omitted = most "
                          "recent complete week")
    ap.add_argument("--year", type=int, default=None,
                     help="year for an 'M.W' label (default: current year)")
    args = ap.parse_args()
    try:
        week_start, week_end = week_range(args.arg, year=args.year)
    except ValueError as e:
        print("ERR: %s" % e, file=sys.stderr)
        sys.exit(1)
    if week_end > date.today():
        print("WARN: resolved week ends %s, which is in the future"
              % week_end.isoformat(), file=sys.stderr)
    print("%s\t%s" % (week_start.isoformat(), week_end.isoformat()))


if __name__ == "__main__":
    main()
