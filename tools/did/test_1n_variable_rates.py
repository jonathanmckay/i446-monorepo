#!/usr/bin/env python3
"""Feature (2026-07-25): 1n+ variable habits follow the sheet's row-5 points
formulas (base + rate×minutes), and "long o314" aliases to the 长o314 header.

Sheet source (1n+ row 5): 业写/s897/family/relax/s+hcbp = "1/m";
长冥想/长o314 = ".5/m"; 一起饭/AoS = "15+1/m". Fixed-point columns are
untouched. Both value paths must agree: route_items (explicit "<habit> N"
minutes) and apply_timer_minutes (minutes from the stopped Toggl timer).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("df_1n", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["df_1n"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def df():
    return _load()


HEADERS = {"0n": {}, "1n": {"长冥想": "AB", "长o314": "V", "aos": "AE",
                            "一起饭": "AC", "业写": "AD", "1 cal": "R"}}
TQ = {"0neon": [], "夜neon": [], "1neon": [], "today": []}


def _route_one(df, text):
    items = df.parse_input(text)
    res = df.route_items(items, HEADERS, TQ, skip_todoist=True)
    assert len(res) == 1
    return res[0]


# ── variable_1n_points formula ───────────────────────────────────────────────

@pytest.mark.parametrize("habit,minutes,expected", [
    ("长冥想", 60, 30),     # .5/m
    ("长o314", 30, 15),     # .5/m
    ("aos", 45, 60),        # 15 + 1/m
    ("一起饭", 90, 105),    # 15 + 1/m
    ("业写", 60, 60),       # 1/m (default rate)
    ("s897", 120, 120),     # 1/m
])
def test_formula(df, habit, minutes, expected):
    assert df.variable_1n_points(habit, minutes) == expected


# ── routing path: "<habit> <minutes>" ────────────────────────────────────────

def test_route_rated_habit_uses_rate(df):
    r = _route_one(df, "长冥想 60")
    assert r.step == "1n" and r.is_variable_1n
    assert r.variable_value == 30


def test_route_base_plus_rate(df):
    r = _route_one(df, "AoS 45")
    assert r.variable_value == 60


def test_route_bare_completion_falls_back_to_base(df):
    assert _route_one(df, "AoS").variable_value == 15
    assert _route_one(df, "一起饭").variable_value == 15
    # No base, no minutes → None (write nothing rather than a wrong value)
    assert _route_one(df, "长冥想").variable_value is None


def test_route_explicit_override_wins(df):
    items = df.parse_input("一起饭 [40]")
    r = df.route_items(items, HEADERS, TQ, skip_todoist=True)[0]
    assert r.variable_value == 40


def test_fixed_point_column_unaffected(df):
    r = _route_one(df, "1 cal")
    assert r.step == "1n" and not r.is_variable_1n


# ── long o314 alias ──────────────────────────────────────────────────────────

def test_long_o314_alias_routes_to_chinese_header(df):
    assert df.ONENEON_ALIASES["long o314"] == "长o314"
    r = _route_one(df, "long o314 30")
    assert r.step == "1n" and r.col_letter == "V"
    assert r.variable_value == 15  # .5/m


# ── timer path must apply the same formula ───────────────────────────────────

def test_apply_timer_minutes_uses_rate_formula(df):
    r = _route_one(df, "long o314")
    df.apply_timer_minutes([r], {"description": "long o314", "minutes": 40})
    assert r.variable_value == 20, ".5/m must apply to timer minutes too"

    r2 = _route_one(df, "AoS")
    df.apply_timer_minutes([r2], {"description": "aos", "minutes": 20})
    assert r2.variable_value == 35  # 15 + 20


def test_apply_timer_minutes_respects_explicit_value(df):
    r = _route_one(df, "长冥想 60")
    df.apply_timer_minutes([r], {"description": "长冥想", "minutes": 10})
    assert r.variable_value == 30, "explicit minutes must not be overwritten"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
