#!/usr/bin/env python3
"""focus.py: longest work run and deep minutes per day from Toggl entries."""
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import focus  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")
IDS = {1: "i9", 2: "m5x2", 3: "xk87"}


def e(hhmm, mins, pid, desc, day="2026-11-23"):
    return {"start": f"{day}T{hhmm}:00-08:00", "duration": mins * 60,
            "project_id": pid, "description": desc}


def test_same_task_across_a_short_gap_is_one_run():
    out = focus.daily_focus([e("10:00", 20, 1, "deck"), e("10:23", 20, 1, "Deck")], TZ, IDS)
    assert out == {"2026-11-23": {"longest": 40, "deep": 40, "work": 40}}


def test_a_different_task_or_a_long_gap_starts_a_new_run():
    out = focus.daily_focus([e("10:00", 20, 1, "deck"), e("10:20", 10, 1, "ibx"),
                             e("10:30", 20, 1, "deck"), e("11:30", 30, 1, "deck")], TZ, IDS)
    assert out["2026-11-23"] == {"longest": 30, "deep": 30, "work": 80}


def test_a_non_work_entry_in_between_breaks_the_run():
    out = focus.daily_focus([e("10:00", 20, 2, "p&l"), e("10:20", 2, 3, "kids"),
                             e("10:22", 20, 2, "p&l")], TZ, IDS)
    assert out["2026-11-23"] == {"longest": 20, "deep": 0, "work": 40}


def test_non_work_days_and_bad_rows_are_left_out():
    out = focus.daily_focus([e("10:00", 60, 3, "lego"), {"start": "", "duration": 60},
                             {"start": "2026-11-23T10:00:00-08:00", "duration": -1, "project_id": 1}],
                            TZ, IDS)
    assert out == {}


def test_runs_do_not_cross_days():
    out = focus.daily_focus([e("23:40", 19, 1, "deck", "2026-11-22"),
                             e("00:00", 30, 1, "deck", "2026-11-23")], TZ, IDS)
    assert out["2026-11-22"]["longest"] == 19 and out["2026-11-23"]["longest"] == 30
