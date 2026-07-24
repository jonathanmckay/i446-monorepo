"""Regression (2026-07-24): "going back two days, the header doesn't fully
update".

Each settled day-nav press spawns _bg_fetch(fetch_points) and
_bg_fetch(fetch_habits_today) on separate daemon threads; two presses ~1s apart
leave two of each racing over ssh, and whichever finished LAST committed to
STATE — so day −1's points/habit strip could land while day −2 was on
screen. Both fetchers must discard their result when the viewed day changed
while the slow read was in flight.
"""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_navrace", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_navrace"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Proc:
    def __init__(self, stdout):
        self.returncode = 0
        self.stdout = stdout


def _fake_run_flipping_view(mod, stdout):
    """subprocess.run stand-in: simulates the user navigating to today while
    the ssh read for the previously-viewed day is still in flight."""
    def run(*a, **k):
        mod.STATE.day_offset = 0
        return _Proc(stdout)
    return run


def test_points_fetch_discards_result_after_day_nav(monkeypatch):
    mod = _load_tui()
    yesterday = dt.datetime.now(mod.TZ).date() - dt.timedelta(days=1)
    mod.STATE.day_offset = -1
    mod.STATE.points_day = yesterday
    mod.STATE.today_points = 42
    mod.STATE.block_points = {"卯": 42}
    # D=999 with a consistent P:Y sum so, WITHOUT the guard, the stale read
    # would pass the trustworthiness gate and commit 999.
    parts = ["999"] + [""] * 9 + ["999"] + [""] * 9 + [""] * 9
    monkeypatch.setattr("subprocess.run",
                        _fake_run_flipping_view(mod, "|".join(parts)))
    mod.fetch_points()
    assert mod.STATE.today_points == 42, \
        "stale day's Σ must not land after navigating away"
    assert mod.STATE.block_points == {"卯": 42}


def test_habits_fetch_discards_result_after_day_nav(monkeypatch):
    mod = _load_tui()
    mod.STATE.day_offset = -1
    sentinel = [("0l", 1.0)]
    mod.STATE.habits_today = sentinel
    mod.STATE.habits_ytd = {"o314": -5.0}
    stdout = "0l\t1|hiit\t||" + "|".join("1" for _ in mod.HABIT_YTD_CELLS)
    monkeypatch.setattr(mod.subprocess, "run",
                        _fake_run_flipping_view(mod, stdout))
    mod.fetch_habits_today()
    assert mod.STATE.habits_today is sentinel, \
        "stale day's habit strip must not land after navigating away"
    assert mod.STATE.habits_ytd == {"o314": -5.0}


def test_fetchers_still_commit_when_view_unchanged(monkeypatch):
    mod = _load_tui()
    mod.STATE.day_offset = 0
    mod.STATE.points_day = dt.datetime.now(mod.TZ).date()
    mod.STATE.today_points = 42
    parts = ["999"] + [""] * 9 + ["999"] + [""] * 9 + [""] * 9
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Proc("|".join(parts)))
    mod.fetch_points()
    assert mod.STATE.today_points == 999, \
        "guard must only drop results when the viewed day actually changed"
