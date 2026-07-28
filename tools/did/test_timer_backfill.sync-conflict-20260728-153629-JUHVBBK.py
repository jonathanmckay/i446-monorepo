#!/usr/bin/env python3
"""Tests for variable-task time backfill from a running Toggl timer.

Regression (2026-06-06): picking a variable task in dtd without typing the
time wrote the default (1 min for 0n, static default for 1n+) even when a
matching Toggl timer was running with the real elapsed time. did-fast now
parses the stopped timer's duration and backfills it.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("did_fast", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def df():
    return _load()


# ── duration parsing ────────────────────────────────────────────────────────

@pytest.mark.parametrize("output,expected", [
    ("Stopped: 16:06-16:39 Jason Beaumont @i9 (33m) [id:1]", 33),
    ("Stopped: 0t @n156 (48min) [id:2]", 48),
    ("Stopped: ibx @i9 (1h03m) [id:3]", 63),
    ("Stopped: thing (2h) [id:4]", 120),
    ("Stopped: weird no duration", None),
])
def test_parse_stop_minutes(df, output, expected):
    assert df._parse_stop_minutes(output) == expected


# ── backfill ────────────────────────────────────────────────────────────────

def _mk(df, name, step, write_value=None, variable_value=None,
        time_value=None, points_override=None, bonus_points=None):
    item = df.ParsedItem(raw=name, name=name, time_value=time_value,
                         points_override=points_override,
                         bonus_points=bonus_points)
    r = df.RouteResult(item=item, step=step)
    r.write_value = write_value
    if variable_value is not None:
        r.variable_value = variable_value
    if step == "1n":
        r.is_variable_1n = True
    return r


def test_0n_variable_backfilled_from_timer(df):
    r = _mk(df, "xk22", "0n", write_value=1)
    df.apply_timer_minutes([r], {"description": "xk22", "minutes": 37})
    assert r.write_value == 37
    assert r.item.time_value == 37


def test_explicit_time_wins_over_timer(df):
    r = _mk(df, "xk22", "0n", write_value=50, time_value=50)
    df.apply_timer_minutes([r], {"description": "xk22", "minutes": 37})
    assert r.write_value == 50


def test_non_matching_timer_ignored(df):
    r = _mk(df, "xk22", "0n", write_value=1)
    df.apply_timer_minutes([r], {"description": "bnet testing", "minutes": 37})
    assert r.write_value == 1


def test_1n_variable_backfilled_with_bonus(df):
    r = _mk(df, "一起饭", "1n", variable_value=30, bonus_points=15)
    df.apply_timer_minutes([r], {"description": "一起饭", "minutes": 25})
    assert r.variable_value == 40  # 25 min + 15 bonus
    assert r.item.time_value == 25


def test_no_timer_is_noop(df):
    r = _mk(df, "xk22", "0n", write_value=1)
    df.apply_timer_minutes([r], None)
    df.apply_timer_minutes([r], {"description": "xk22", "minutes": None})
    assert r.write_value == 1


def test_non_variable_0n_not_touched(df):
    r = _mk(df, "0l", "0n", write_value=1)
    df.apply_timer_minutes([r], {"description": "0l", "minutes": 42})
    assert r.write_value == 1


# ── variable-domain (bball/run/walk/...) backfill ───────────────────────────
# Regression (2026-06-07): VARIABLE_DOMAIN tasks (step "variable") were not
# backfilled — completing `bball` from dtd with a running timer logged 0
# points. apply_timer_minutes now covers the "variable" step too.

def _mk_var(df, name, fen_points=0, **kw):
    r = _mk(df, name, "variable", **kw)
    r.fen_points = fen_points
    return r


def test_variable_domain_backfilled_from_timer(df):
    r = _mk_var(df, "bball")
    df.apply_timer_minutes([r], {"description": "bball", "minutes": 45})
    assert r.fen_points == 45
    assert r.item.time_value == 45


def test_variable_domain_bonus_added(df):
    r = _mk_var(df, "bball", bonus_points=10)
    df.apply_timer_minutes([r], {"description": "bball", "minutes": 45})
    assert r.fen_points == 55


def test_variable_domain_explicit_wins(df):
    # User typed "bball 30" → time_value set → timer must not override.
    r = _mk_var(df, "bball", fen_points=30, time_value=30)
    df.apply_timer_minutes([r], {"description": "bball", "minutes": 45})
    assert r.fen_points == 30


def test_variable_domain_non_matching_timer_ignored(df):
    r = _mk_var(df, "run")
    df.apply_timer_minutes([r], {"description": "bball", "minutes": 45})
    assert r.fen_points == 0


def test_non_domain_variable_not_touched(df):
    # A project-override variable task (not in VARIABLE_DOMAIN) is left alone.
    r = _mk_var(df, "some custom task")
    df.apply_timer_minutes([r], {"description": "some custom task", "minutes": 45})
    assert r.fen_points == 0


# ── manual path parity: "bball 30" must log 30, not 0 ───────────────────────
# Regression (2026-06-07): the variable route honored [N] and time_range but
# ignored a typed trailing number (time_value), so `/did bball 30` logged 0.

def test_variable_domain_typed_minutes_route_to_points(df):
    items = df.parse_input("bball 30")
    routes = df.route_items(items, {"0n": {}, "1n": {}}, {}, skip_todoist=True)
    assert routes[0].step == "variable"
    assert routes[0].fen_points == 30
