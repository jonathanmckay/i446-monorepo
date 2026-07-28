"""Regression test (2026-06-22): the in-progress block showed no 分 in janus.
Its 0分 G:O cell is the live residual formula =D-SUM(locked), which
fetch_points now reads as a VALUE (not a formula), so block_points holds
every nonzero block, current one included, straight from Neon.

An EARLIER fix (since removed, 2026-07-20) instead reconstructed the running
total as Σ_today minus the locked literal blocks for the brief cold-start
window before the first successful read. That reconstruction was itself the
source of at least two "impossible" block values shown on screen (5064分
2026-07-03, 6932分 2026-07-20) — user feedback: "all you have to do is pull
from neon." There is no other path now: before the first successful read,
_block_display_pts returns 0, never a guess.
"""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_curblk", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_curblk"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_locked_block_uses_literal_not_residual():
    m = _load_tui()
    m.STATE.today_points = 300
    m.STATE.block_points = {"卯": 60, "辰": 40}
    # 辰 is locked → its literal, regardless of which block is "now".
    assert m._block_display_pts("辰") == 40


def test_cold_start_shows_zero_not_a_guess(monkeypatch):
    """Before the first successful Neon read, block_points is EMPTY -- the
    current clock block must show 0, not a reconstructed running total (the
    reconstruction was the actual source of the 5064分/6932分 bugs)."""
    m = _load_tui()
    m.STATE.today_points = 300
    m.STATE.block_points = {}  # no successful read yet
    fixed = dtm.datetime(2026, 6, 22, 10, 30, tzinfo=m.TZ)  # now = 午

    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(m.dt, "datetime", _DT)
    assert m._block_display_pts("午") == 0, "cold start must show 0, never a guessed value"


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


def test_current_block_running_pts_reconstruction_is_gone():
    """Structural: the fragile Σ-minus-locked reconstruction accessor must
    not come back -- the direct Neon read is the only source now (user
    request 2026-07-20: "all you have to do is pull from neon")."""
    src = (HERE / "janus.py").read_text()
    assert "_current_block_running_pts" not in src
    assert "self.block_running_pts = 0" not in src
