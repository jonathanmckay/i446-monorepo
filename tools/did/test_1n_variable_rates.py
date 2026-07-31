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


def test_route_bare_completion_falls_back_to_base(df, monkeypatch):
    # No-minutes fallback must not depend on live Toggl (the API key now
    # loads inside did-fast, so an unmocked lookup can find real entries)
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: None)
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


# ── "[1/m]" display annotation (card contents, 2026-07-25) ───────────────────

RATE_HEADERS = {"0n": {}, "1n": {"1 kids nature": "AL", "family": "W",
                                 "s897": "X"}}


# (N) durations never reach did-fast — dtd's sed cleaners strip them before
# the FIFO — so the realistic inputs carry only the name + rate marker.
@pytest.mark.parametrize("raw,name", [
    ("1 kids nature [1/m]", "1 kids nature"),
    ("family [1/m]", "family"),
    ("s897 [.5/m]", "s897"),
    ("一起饭 [15+1/m] 90", "一起饭"),
])
def test_parse_strips_rate_annotation(df, raw, name):
    item = df.parse_input(raw)[0]
    assert item.name == name
    assert item.points_override is None, "[1/m] is not a points override"


def test_rate_annotated_card_routes_and_computes(df):
    items = df.parse_input("1 kids nature [1/m] 90")
    r = df.route_items(items, RATE_HEADERS, TQ, skip_todoist=True)[0]
    assert r.step == "1n" and r.is_variable_1n
    assert r.variable_value == 90  # 1/m


def test_dtd_cleaners_strip_rate_annotation():
    """dtd's shell cleaners must eat ' [1/m]' like numeric [N]: it must never
    reach the Toggl timer description or the completion FIFO name."""
    src = (_HERE / "dtd.sh").read_text()
    heredoc = r"s/ *\\[[0-9]*\\]//g; s/ *\\[[0-9.+]*\\/m\\]//g"
    plain = r"s/ *\[[0-9]*\]//g; s/ *\[[0-9.+]*\/m\]//g"
    # 2026-07-31: enter.sh dropped its own `clean=` computation -- it forwards
    # the raw resolved task straight to $START (which cleans it itself), one
    # fewer heredoc cleaner than before ("always opt+enter to mark done").
    assert src.count(heredoc) == 8, "all heredoc cleaners must strip [1/m]"
    assert src.count(plain) == 3, "all plain cleaners must strip [1/m]"
    assert r"| *\[[0-9.+]*/m\]" in src, "list strip_ann must strip [1/m]"


def test_dtd_rjust_treats_rate_marker_as_estimate():
    """[1/m] must right-justify into the estimate column like numeric [N]
    (bug 2026-07-25: rate-annotated cards rendered left-stuck)."""
    src = (_HERE / "dtd.sh").read_text()
    assert r"\[[0-9.+]*/m\]" in src.split("_EST_TOK = ")[1].split("\n")[0], (
        "_EST_TOK must include the [1/m] rate marker")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── 长 (threshold) habits: min 30m, points to BOTH week cell and 0分 (2026-07-30)

def test_threshold_habit_below_minimum_skips(df):
    r = _route_one(df, "长o314 20")
    assert r.step == "skipped"
    assert "20m" in r.error and "30m" in r.error


def test_threshold_habit_at_minimum_credits(df):
    r = _route_one(df, "长冥想 30")
    assert r.step == "1n"
    assert r.variable_value == 15          # 30 × .5
    assert r.write_value == 15             # week cell gets POINTS, not minutes
    assert r.fen_col == "V"                # hcm domain column


def test_threshold_habit_points_are_half_minutes(df):
    r = _route_one(df, "长o314 36")
    assert r.step == "1n"
    assert r.variable_value == 18 and r.write_value == 18
    assert r.fen_col == "V"


def test_threshold_habit_pulls_toggl_minutes_from_base_activity(df, monkeypatch):
    """长o314's minutes come from Toggl entries named 'o314', not '长o314'."""
    calls = []
    monkeypatch.setattr(df, "toggl_minutes_for",
                        lambda name: calls.append(name) or 36)
    r = _route_one(df, "长o314")
    assert calls == ["o314"]
    assert r.step == "1n" and r.write_value == 18


def test_non_threshold_variable_still_records_minutes(df):
    """The week-cell-gets-points rule applies ONLY to 长 habits."""
    r = _route_one(df, "业写 60")
    assert r.step == "1n"
    assert r.write_value == 60             # minutes in the cell
    assert r.variable_value == 60          # 1/m


def test_timer_path_threshold_writes_points(df):
    items = df.parse_input("长冥想")
    res = df.route_items(items, HEADERS, TQ, skip_todoist=True)
    r = res[0]
    assert r.step == "1n"  # dtd path defers the threshold check to the timer
    df.apply_timer_minutes(res, {"description": "长冥想", "minutes": 40})
    assert r.variable_value == 20
    assert r.write_value == 20


def test_timer_path_threshold_below_minimum_skips(df):
    items = df.parse_input("长o314")
    res = df.route_items(items, HEADERS, TQ, skip_todoist=True)
    df.apply_timer_minutes(res, {"description": "长o314", "minutes": 12})
    r = res[0]
    assert r.step == "skipped"
    assert "12m" in r.error
