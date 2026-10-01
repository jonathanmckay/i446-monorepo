"""Fiscal week labels for the Neon '1n+' sheet (column B: '1.1' … '12.5').

The sheet's rows are NOT calendar week-of-month. They are 52 consecutive
Sunday-anchored weeks per year, bucketed 4-4-5 per quarter: every third
label-month (3, 6, 9, 12) holds 5 rows, every other month holds 4. Week
'1.1' starts on the first Sunday on/after Jan 1 (2026-01-04). Sundays fill
the slots in pure chronological order, so a calendar month with 5 real
Sundays that is not a quarter-end month does not get a '.5' row: its 5th
Sunday takes slot 1 of the next label-month (2026-08-30 → '9.1',
2026-09-06 → '9.2', … 2026-09-27 → '9.5', 2026-10-04 → '10.1').

Bug (2026-10-01): /did's two 1n+ writers (tools/did/did-fast.py and
tools/did/run.py) used "which Sunday of the calendar month" instead, which
agrees with the sheet most of the year and silently diverges after any
non-quarter-end month with 5 Sundays. From 2026-08-30 ('8.5' → not found →
write error) through 2026-10-03 every weekly habit landed one row above
its real week (Sep 27 week → row '9.4' instead of '9.5').

Same rule as tools/1s/1s-survey.py:week_row_label and the per-skill
week_calc.py copies in 1s897/1i9/1m5x2; this is the shared import for
tools that already depend on lib/neon.
"""
from __future__ import annotations

from datetime import date, timedelta

# Rows per label-month, Jan..Dec. Sums to 52.
WEEK_QUOTA = (4, 4, 5, 4, 4, 5, 4, 4, 5, 4, 4, 5)


def first_sunday_of_year(year: int) -> date:
    d = date(year, 1, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7)


def week_sunday(d: date) -> date:
    """The Sunday that starts the Sun-Sat week containing `d`."""
    return d - timedelta(days=(d.weekday() + 1) % 7)  # weekday(): Mon=0..Sun=6


def fiscal_week_label(d: date) -> str:
    """'M.W' label of the 1n+ row for the week containing `d`."""
    sunday = week_sunday(d)
    year = sunday.year
    first = first_sunday_of_year(year)
    if sunday < first:
        year -= 1
        first = first_sunday_of_year(year)
    ordinal = (sunday - first).days // 7 + 1  # 1-indexed Sunday-of-year
    cum = 0
    for month, quota in enumerate(WEEK_QUOTA, start=1):
        if ordinal <= cum + quota:
            return "%d.%d" % (month, ordinal - cum)
        cum += quota
    return "1.1"  # 53rd-Sunday year: rolls into next year's week 1
