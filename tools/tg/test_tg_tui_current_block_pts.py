"""Regression test (2026-06-22): the in-progress block showed no 分 in tg-tui.
Its 0分 G:O cell is the live residual formula =D-SUM(locked), which fetch_points
skips, so block_points never holds the current block and its header read 0. Fix:
reconstruct the running total as Σ_today minus the locked literal blocks and show
it on the current block's header only (a future/next block stays 0).
"""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_curblk", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_curblk"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_running_pts_accessor_returns_precomputed():
    m = _load_tui()
    m.STATE.block_running_pts = 200  # fetch_points computes Σ − locked once
    assert m._current_block_running_pts() == 200


def test_locked_block_uses_literal_not_residual():
    m = _load_tui()
    m.STATE.today_points = 300
    m.STATE.block_points = {"卯": 60, "辰": 40}
    # 辰 is locked → its literal, regardless of which block is "now".
    assert m._block_display_pts("辰") == 40


def test_current_block_gets_running_total(monkeypatch):
    m = _load_tui()
    m.STATE.today_points = 300
    m.STATE.block_points = {"卯": 60, "辰": 40}
    m.STATE.block_running_pts = 200  # precomputed residual for the current block
    # Pin "now" into a block (午 = hours 10-11 per BLOCKS) that is NOT locked.
    fixed = dtm.datetime(2026, 6, 22, 10, 30, tzinfo=m.TZ)

    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(m.dt, "datetime", _DT)
    assert m._block_display_pts("午") == 200, "current block shows reconstructed running 分"


def test_future_block_shows_zero(monkeypatch):
    m = _load_tui()
    m.STATE.today_points = 300
    m.STATE.block_points = {"卯": 60, "辰": 40}
    fixed = dtm.datetime(2026, 6, 22, 10, 30, tzinfo=m.TZ)  # now = 午

    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(m.dt, "datetime", _DT)
    # 戌 is a later (future) block, unlocked → must NOT inherit the residual.
    assert m._block_display_pts("戌") == 0, "a future block has earned nothing yet"
