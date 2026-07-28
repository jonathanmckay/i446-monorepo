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


# ── 1n+ redesign (2026-07-27): cell = minutes, 0分 = points ─────────────────

def test_1n_cell_write_is_minutes_not_points(df):
    """The week cell records how long the habit took (1 if unknown) — never a
    points row read. Points reach 0分 via the row-5 reference instead."""
    r = df.RouteResult(item=df.ParsedItem(raw="1 -2g", name="1 -2g"),
                       step="1n", col_letter="K", write_value=25)
    script = df.build_1n_script([r], "7.4")
    assert 'to 25' in script
    assert '"K3"' not in script and '"K5"' not in script


def test_1n_script_has_no_same_month_week_fallback(df):
    """A missing week row must ERROR, not silently credit the last row of the
    same month (a Sunday completion before the new week's row exists landed
    on LAST week's row)."""
    r = df.RouteResult(item=df.ParsedItem(raw="1 -2g", name="1 -2g"),
                       step="1n", col_letter="K", write_value=1)
    script = df.build_1n_script([r], "7.4")
    assert "fallbackRow" not in script
    assert "ERROR: week 7.4 not found" in script


def test_1n_fen_append_references_row5_expected_points(df):
    script = df.build_1n_0fen_script([("S", "I", "5")], "7/27")
    assert "+'1n+'!I5" in script


H1N = {"0n": {}, "1n": {"1 m5x2": "I", "1 -2g": "K"}}


def test_1n_route_minutes_from_toggl_and_fen_fallback(df, monkeypatch):
    """Cell minutes come from today's matching Toggl entries when untyped, and
    '1 m5x2' (absent from the hand-kept fen map — bug 2026-07-27: its points
    never reached today) resolves its 0分 column from the domain token."""
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 42)
    r = df.route_items([df.ParsedItem(raw="1 m5x2", name="1 m5x2")], H1N, TQ)[0]
    assert r.step == "1n" and r.write_value == 42
    assert r.fen_col == "S"


def test_1n_route_minutes_default_1_when_unknown(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: None)
    r = df.route_items([df.ParsedItem(raw="1 -2g", name="1 -2g")], H1N, TQ)[0]
    assert r.write_value == 1


# ── {N} goals must not double-credit the domain column (2026-07-27) ─────────

def test_curly_only_task_yields_zero_domain_points(df):
    """POINTS_RE matched {N} as well as [N], so a completed {N} goal credited
    its domain column IN ADDITION to the Q (0g) credit from curly_points —
    e.g. hcb got +20+20+10 duplicates of three goals' Q credits."""
    assert df.POINTS_RE.search("follow meal plan {20}") is None
    assert df.POINTS_RE.search("Elliot ES R&R (20) [60]").group(1) == "60"
    goal = {"id": "g1", "content": "follow meal plan {20}",
            "labels": ["#0g", "hcb"], "recurring": False}
    tq = {"0neon": [goal], "夜neon": [], "1neon": []}
    item = df.ParsedItem(raw="follow meal plan {20}", name="follow meal plan",
                         curly_points=20)
    r = df.route_items([item], {"0n": {}, "1n": {}}, tq)[0]
    assert r.step == "todoist"
    assert r.fen_points == 0, "{N} must flow ONLY via curly_points → Q"
