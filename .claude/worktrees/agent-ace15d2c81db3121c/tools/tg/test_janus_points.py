"""Regression tests for janus per-block points.

Bug: per-block 分 were reconstructed from completed-today.json LOGGING
timestamps, so batch-logged work all piled into the current block ("everything
shows in 申"). Neon 0分 has authoritative per-block columns G:O (headed
卯辰巳午未申酉戌亥); fetch_points must read those, with timestamps only as a
fallback when Excel is unreachable.
"""
import datetime as dtm
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_pts", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_pts"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeProc:
    def __init__(self, out, rc=0):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def test_block_points_read_from_neon_g_to_o_columns(monkeypatch):
    m = _load_tui()
    # Σ=902 (= block sum: the live-residual invariant), then 卯..亥:
    # 卯 empty, 辰=3, 巳=247, 午=164.57…, 未=237, 申=250, rest empty
    out = "902||3.0|247.0|164.571428571429|237.0|250.0|||"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(out))
    m.STATE.block_points = {}
    m.fetch_points()
    assert m.STATE.today_points == 902
    assert m.STATE.block_points == {
        "辰": 3, "巳": 247, "午": 165, "未": 237, "申": 250,
    }, "per-block points must come from 0分 G:O, not logging timestamps"


def test_block_points_kept_when_excel_unreachable(monkeypatch):
    """When the Neon read fails, keep the last good per-block values — do NOT
    fall back to completed-today.json timestamps (the 313-in-酉 bug: batch
    logging piles every point into the block it was logged in)."""
    m = _load_tui()

    def _boom(*a, **k):
        raise RuntimeError("ix unreachable")

    monkeypatch.setattr(subprocess, "run", _boom)
    prior = {"酉": 33, "巳": 247}
    m.STATE.block_points = dict(prior)
    m.fetch_points()
    assert m.STATE.block_points == prior, \
        "failed Neon read must not clobber block_points with timestamp guesses"


def test_block_points_no_timestamp_reconstruction(monkeypatch):
    """A successful-but-empty Neon read (genuine zero day) sets {} — and the
    completed-today.json timestamp path must be gone entirely."""
    m = _load_tui()
    # Successful read, all blocks empty
    out = "0|||||||||"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(out))
    m.STATE.block_points = {"酉": 999}  # stale value must be cleared on a good read
    m.fetch_points()
    assert m.STATE.block_points == {}, "good read with no points must clear to {}"


def test_residual_formula_blocks_are_skipped(monkeypatch):
    """Block columns lock sequentially to literals; an unlocked block is the
    residual `=D-SUM(locked)` which dumps the whole day into the first unlocked
    block (the "everything piles into 巳 / 2392分" bug). G:O are read as
    FORMULAS, so a cell still starting with '=' must be skipped — only locked
    literal earnings count. Σ (col D, read as a value) is unaffected."""
    m = _load_tui()
    # Σ=353; 卯=60 and 辰=0 locked literals; 巳 onward still residual formulas.
    out = (
        "353.05|60|0"
        "|=D164-SUM(G164,H164)"
        "|=D164-SUM(G164,H164,I164,)"
        "|=D164-SUM(G164,H164,I164,J164)"
        "|=D164-SUM(G164,H164,I164,J164,K164)"
        "|=D164-SUM(G164,H164,I164,J164,K164,L164)"
        "|=D164-SUM(G164,H164,I164,J164,K164,L164,M164)"
        "|=D164-SUM(G164,H164,I164,J164,K164,L164,M164,N164)"
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(out))
    m.STATE.block_points = {"巳": 2392}  # the stale residual spike must be cleared
    m.fetch_points()
    assert m.STATE.today_points == 353
    assert m.STATE.block_points == {"卯": 60}, (
        "residual =D-SUM(...) cells must not be attributed as per-block earnings"
    )
