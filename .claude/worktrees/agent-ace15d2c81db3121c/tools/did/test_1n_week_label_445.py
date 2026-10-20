#!/usr/bin/env python3
"""Regression (2026-10-01): "1n habits are not recording to the 9.5 week".

The Neon 1n+ sheet's column B is a 4-4-5 fiscal-week ladder: 52 Sunday-
anchored weeks per year filled chronologically, with label-months 3/6/9/12
holding 5 rows and every other month 4 (sheet rows 6..57 read 1.1 1.2 1.3
1.4 2.1 ... 3.5 4.1 ... 9.1 ... 9.5 10.1 ... 12.5). Both /did 1n+ writers
(did-fast.py:calc_week_mw and run.py:_calc_mw) instead computed "which
Sunday of the calendar month", which agrees with the sheet only until a
non-quarter-end month has 5 Sundays. 2026-08-30 is the 5th Sunday of
August: the old formula produced '8.5' (no such row -> write error) and
then '9.1' for Sep 6 (sheet: '9.2'), ... '9.4' for the Sep 27 week whose
real row is '9.5'. Every weekly habit from 2026-08-30 to 2026-10-03 landed
one row above its week; the daemon ledger (vault/g245/neon-ledger/2026-09)
shows run.py writes at rows 40/41/42/43 for weeks that belong on 41/42/43/44.

Fix: both writers now call lib/neon/weeks.fiscal_week_label, the same rule
tools/1s/1s-survey.py:week_row_label already used.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

from neon import weeks  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# The 1n+ sheet's column B, rows 6..57, as dumped from Neon分v12.2.xlsx on
# 2026-10-01 (row 6 = Sunday 2026-01-04).
SHEET_COL_B = (
    "1.1 1.2 1.3 1.4 2.1 2.2 2.3 2.4 3.1 3.2 3.3 3.4 3.5 "
    "4.1 4.2 4.3 4.4 5.1 5.2 5.3 5.4 6.1 6.2 6.3 6.4 6.5 "
    "7.1 7.2 7.3 7.4 8.1 8.2 8.3 8.4 9.1 9.2 9.3 9.4 9.5 "
    "10.1 10.2 10.3 10.4 11.1 11.2 11.3 11.4 12.1 12.2 12.3 12.4 12.5"
).split()


def test_sheet_col_b_is_a_445_ladder_not_calendar_weeks():
    """Sanity on the fixture itself: every 2026 Sunday maps to the sheet's
    rows in order. Week of 2026-08-30 is '9.1', not '8.5'."""
    assert len(SHEET_COL_B) == 52
    sunday = date(2026, 1, 4)
    for label in SHEET_COL_B:
        assert weeks.fiscal_week_label(sunday) == label, (sunday, label)
        sunday = date.fromordinal(sunday.toordinal() + 7)
    assert weeks.fiscal_week_label(date(2026, 8, 30)) == "9.1"


def test_did_fast_calc_week_mw_oct_1_2026_is_9_5():
    did_fast = _load("did_fast_week_label", HERE / "did-fast.py")
    assert did_fast.calc_week_mw(date(2026, 10, 1)) == "9.5"
    assert did_fast.calc_week_mw(date(2026, 9, 27)) == "9.5"   # Sunday
    assert did_fast.calc_week_mw(date(2026, 10, 3)) == "9.5"   # Saturday
    assert did_fast.calc_week_mw(date(2026, 10, 4)) == "10.1"
    assert did_fast.calc_week_mw(date(2026, 8, 30)) == "9.1"   # old: '8.5'
    assert did_fast.calc_week_mw(date(2026, 6, 1)) == "6.1"    # old: '5.5'


def test_did_fast_calc_week_mw_matches_sheet_for_every_day_of_2026():
    """Any day in a week maps to that week's row, across the whole year."""
    did_fast = _load("did_fast_week_label_sweep", HERE / "did-fast.py")
    d = date(2026, 1, 4)
    for i, label in enumerate(SHEET_COL_B):
        for offset in range(7):
            day = date.fromordinal(d.toordinal() + i * 7 + offset)
            assert did_fast.calc_week_mw(day) == label, (day, label)


def test_run_calc_mw_scans_for_9_5_on_oct_1_2026():
    run = _load("did_run_week_label", HERE / "run.py")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 1, 8, 46)

    scripts = []

    class FakeCompleted:
        returncode = 0
        stdout = "44\n"
        stderr = ""

    def fake_run(*args, **kwargs):
        scripts.append(kwargs.get("input", ""))
        return FakeCompleted()

    with patch.object(run, "datetime", FrozenDatetime), \
         patch.object(run.subprocess, "run", side_effect=fake_run):
        mw, row = run._calc_mw("10/1")

    assert mw == 9.5
    assert row == 44
    assert len(scripts) == 1
    assert 'cellVal = "9.5"' in scripts[0], scripts[0]
    assert '"9.4"' not in scripts[0]


def test_run_calc_mw_agrees_with_did_fast_and_1s_survey():
    """All three M.W producers must label the same week identically."""
    run = _load("did_run_week_label_agree", HERE / "run.py")
    did_fast = _load("did_fast_week_label_agree", HERE / "did-fast.py")
    s1 = _load("s1_week_label_agree", HERE.parent / "1s" / "1s-survey.py")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 1, 8, 46)

    class FakeCompleted:
        returncode = 0
        stdout = "7\n"
        stderr = ""

    with patch.object(run, "datetime", FrozenDatetime), \
         patch.object(run.subprocess, "run", return_value=FakeCompleted()):
        for md in ("8/30", "9/6", "9/27", "10/1", "5/31", "6/1", "4/24"):
            m, d = (int(x) for x in md.split("/"))
            day = date(2026, m, d)
            expected = s1.week_row_label(weeks.week_sunday(day))
            assert did_fast.calc_week_mw(day) == expected, (md, expected)
            mw, _ = run._calc_mw(md)
            assert f"{mw}" == expected, (md, mw, expected)
