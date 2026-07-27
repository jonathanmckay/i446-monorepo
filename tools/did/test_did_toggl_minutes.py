#!/usr/bin/env python3
"""Feature (2026-07-24): completing a 0₦ habit with no typed value records the
minutes it actually took, pulled from today's same-named Toggl entries
(including the running one), instead of a flat 1. Falls back to 1 when Toggl
is unreachable or has no matching entry; an explicit typed value (including
the N/A 0) always wins and must not consult Toggl at all.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "did_fast_toggl", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_toggl"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def df():
    mod = _load()
    yield mod
    mod._TOGGL_TODAY = None


HEADERS = {"0n": {"新闻": 14}, "1n": {}}
TQ = {"0neon": [], "夜neon": [], "1neon": []}


def test_toggl_minutes_sums_matching_entries(df):
    df._TOGGL_TODAY = [
        {"description": "新闻", "duration": 600},          # 10 min
        {"description": "新闻", "duration": 300},          # 5 min
        {"description": "other", "duration": 6000},        # ignored
    ]
    assert df.toggl_minutes_for("新闻") == 15


def test_toggl_minutes_includes_running_elapsed(df):
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) - timedelta(minutes=20)) \
        .strftime("%Y-%m-%dT%H:%M:%S+00:00")
    df._TOGGL_TODAY = [{"description": "新闻", "duration": -1, "start": start}]
    assert df.toggl_minutes_for("新闻") in (19, 20, 21)


def test_toggl_minutes_none_when_no_match(df):
    df._TOGGL_TODAY = [{"description": "other", "duration": 600}]
    assert df.toggl_minutes_for("新闻") is None


def test_0n_route_uses_toggl_minutes_when_no_value(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 34)
    item = df.ParsedItem(raw="新闻", name="新闻")
    r = df.route_items([item], HEADERS, TQ)[0]
    assert r.step == "0n" and r.write_value == 34


def test_0n_route_falls_back_to_1(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: None)
    item = df.ParsedItem(raw="新闻", name="新闻")
    r = df.route_items([item], HEADERS, TQ)[0]
    assert r.write_value == 1


def test_explicit_value_skips_toggl(df, monkeypatch):
    def boom(name):
        raise AssertionError("must not consult Toggl for explicit values")
    monkeypatch.setattr(df, "toggl_minutes_for", boom)
    for typed, expected in ((2, 2), (0, 0)):  # 0 = explicit N/A
        item = df.ParsedItem(raw="新闻", name="新闻", time_value=typed)
        r = df.route_items([item], HEADERS, TQ)[0]
        assert r.write_value == expected


# ── 1n+ write script: points row + no wrong-week fallback (2026-07-27) ──────

def test_1n_script_reads_points_from_row5_not_row3(df):
    """Row 3 became the recurrence WEEKDAY in the 2026-07-25 restructure
    (row 5 = expected points); reading row 3 wrote e.g. 2 (Monday) as the
    week's score for /1-2g instead of [20]."""
    r = df.RouteResult(item=df.ParsedItem(raw="1 -2g", name="1 -2g"),
                       step="1n", col_letter="K")
    script = df.build_1n_script([r], "7.4")
    assert '"K5"' in script and '"K3"' not in script


def test_1n_script_has_no_same_month_week_fallback(df):
    """A missing week row must ERROR, not silently credit the last row of the
    same month (a Sunday completion before the new week's row exists landed
    on LAST week's row)."""
    r = df.RouteResult(item=df.ParsedItem(raw="1 -2g", name="1 -2g"),
                       step="1n", col_letter="K")
    script = df.build_1n_script([r], "7.4")
    assert "fallbackRow" not in script
    assert "ERROR: week 7.4 not found" in script
