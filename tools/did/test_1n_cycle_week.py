"""Regression (2026-10-26): weekly cards on their own cycle.

JM: "for -1n habits I don't follow the overall week, I follow that cycle ...
these habits follow a Monday - Monday cycle." A Sunday run of 1 -2g / 1 -1n
(due Mondays) credited the 1n+ row of the Sun-Sat week that Sunday opens
(10.4) and closed the Monday card due the next day; it belongs to the cycle
that is ending (10.3)."""
import ast
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))
from neon import weeks  # noqa: E402

SUN, MON, SAT = date(2026, 10, 25), date(2026, 10, 26), date(2026, 10, 24)


def test_monday_cycle_cards_put_sunday_in_the_ending_week():
    assert weeks.fiscal_week_label(SUN) == "10.4"            # the sheet week Sunday opens
    assert weeks.habit_week_label("1 -2g", SUN) == "10.3"   # the cycle Mon 10/19 - Sun 10/25
    assert weeks.habit_week_label("1 -1n", SUN) == "10.3"
    assert weeks.habit_week_label("1 -2g", MON) == "10.4"   # Monday starts the new cycle
    assert weeks.habit_week_label("1  -1N ", SAT) == "10.3"  # case/space-insensitive
    assert weeks.cycle_start("1 -2g", SUN) == date(2026, 10, 19)


def test_cycle_lookup_folds_hyphens_like_did_fast_header_match():
    # did-fast's header_normalize maps `/did 1-2g` onto the 1 -2g column;
    # the cycle lookup must agree or a Sunday 1-2g lands in week 10.4.
    for alias in ("1-2g", "1 2g", "1 - 2g", "1-1n", "1 —1n"):
        assert weeks.habit_week_label(alias, SUN) == "10.3", alias


def test_other_cards_keep_the_sunday_week():
    assert weeks.habit_week_label("1 m5x2", SUN) == "10.4"
    assert weeks.habit_week_label("1 i9", SAT) == "10.3"
    assert weeks.cycle_start("1 m5x2", SAT) == date(2026, 10, 18)


def _src(name):
    return (HERE / name).read_text()


def test_run_py_uses_the_cycle_for_row_and_close_window():
    s = _src("run.py")
    assert "_calc_mw(target_date, name)" in s
    assert "weeks.cycle_start(name, _target_date_obj(target_date)) + timedelta(days=6)" in s


def test_did_fast_groups_1n_writes_by_cycle_week():
    s = _src("did-fast.py")
    assert "neon_weeks.habit_week_label(_hname, week_date)" in s
    ast.parse(s)
