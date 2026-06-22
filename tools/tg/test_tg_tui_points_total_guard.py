"""Regression test (2026-06-22): the in-progress block showed 4351分 instead of
385. Col D (=SUM(P:Y)) is read mid-recalc during did/daemon writes and returns
garbage (the rejection log captured D=-46 for ~26min, plus high spikes).
fetch_points committed today_points on EVERY read, before the block consistency
guard, so a torn total poisoned the topline — and once the current-block 分
reconstruction (Σ − locked) started depending on the total, that torn read
corrupted the live block. Fix: gate today_points on a plausibility bound and pair
fresh blocks only with a freshly-accepted total.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_totguard", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_totguard"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeProc:
    def __init__(self, out, rc=0):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def _run(monkeypatch, m, out):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(out))
    m.fetch_points()


def test_negative_torn_total_is_rejected(monkeypatch):
    m = _load_tui()
    m.STATE.today_points = 606
    m.STATE.block_points = {"午": 215}
    # The exact dawn torn read from the log: D=-46, every block still residual.
    _run(monkeypatch, m, "-46|=D|=D-G|=D-SUM(G,H)|=D-SUM(G,H,I)|=D|=D|=D|=D|=D")
    assert m.STATE.today_points == 606, "a negative torn total must not be adopted"
    assert m.STATE.block_points == {"午": 215}, "blocks kept when total is torn"


def test_high_spike_total_is_rejected(monkeypatch):
    m = _load_tui()
    m.STATE.today_points = 606
    m.STATE.block_points = {"巳": 3, "午": 215}
    # D spikes to 4351 (the reported bug) while blocks read normally.
    _run(monkeypatch, m, "4351||3|215|=D|=D|=D|=D|=D|=D")
    assert m.STATE.today_points == 606, "an implausibly high total must not be adopted"
    assert m.STATE.block_points == {"巳": 3, "午": 215}, "blocks not paired with a torn total"


def test_plausible_total_and_blocks_are_adopted(monkeypatch):
    m = _load_tui()
    m.STATE.today_points = 0
    m.STATE.block_points = {}
    # D | 卯 | 辰 | 巳=3 | 午=215 | 未… residual
    _run(monkeypatch, m, "606|||3|215|=D|=D|=D|=D|=D")
    assert m.STATE.today_points == 606
    assert m.STATE.block_points == {"巳": 3, "午": 215}


def test_reconstruction_uses_clean_total_after_torn_read(monkeypatch):
    """End-to-end: after a spike read is rejected, the current-block reconstruction
    reflects the last good total, not the garbage."""
    m = _load_tui()
    m.STATE.today_points = 606
    m.STATE.block_points = {"巳": 3, "午": 215}  # locked = 218
    _run(monkeypatch, m, "4351||3|215|=D|=D|=D|=D|=D|=D")
    assert m._current_block_running_pts() == 606 - 218, "must reconstruct from the clean total"
