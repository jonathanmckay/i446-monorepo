#!/usr/bin/env python3
"""Resolve the target Wed-Tue week for /1s897.

No arg      -> the most recent COMPLETE week (week_end <= today).
'M.W' arg   -> the Wed-Tue week containing that label's Sunday. 'M.W' is the
               Sunday-anchored week-of-month label used everywhere else in
               this system (1分+1s, 1n+, xk887): M = the Sunday's month,
               W = which Sunday of that month. It has no year component, so
               this assumes the current year unless --year is given.
ISO date arg -> the Wed-Tue week containing that date.

Prints one line: <week_start ISO>\\t<week_end ISO>
"""
from __future__ import annotations

import argparse
import calendar
import re
import sys
from datetime import date, timedelta

_WEEK_LABEL_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})$")


def sunday_for_week_label(label: str, year: int | None = None) -> date:
    """'M.W' -> that week's Sunday. Raises ValueError if the label doesn't
    parse or W doesn't exist in that month (e.g. '2.6')."""
    m = _WEEK_LABEL_RE.match(label)
    if not m:
        raise ValueError("not a week label: %r" % label)
    month, week = int(m.group(1)), int(m.group(2))
    year = year or date.today().year
    days_in_month = calendar.monthrange(year, month)[1]
    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        if d.weekday() == 6 and (day - 1) // 7 + 1 == week:  # Sunday
            return d
    raise ValueError("no week %s in %d-%02d" % (label, year, month))


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
