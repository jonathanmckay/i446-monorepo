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


def test_spike_under_cap_caught_by_py_crosscheck(monkeypatch):
    """The 1523-on-a-758分-day bug: a torn total below the 2000 cap that a fixed
    bound can't catch. D must equal its own =SUM(P:Y); here it doesn't, so reject.
    Layout: D | 9×G:O | 10×P:Y (P:Y sums to 758, D claims 1523)."""
    m = _load_tui()
    m.STATE.today_points = 758
    m.STATE.block_points = {"午": 200}
    raw = "1523|=D|=D|=D|=D|=D|=D|=D|=D|=D|21|29|377|33|195|34|13|6|50|0"
    _run(monkeypatch, m, raw)
    assert m.STATE.today_points == 758, "D≠SUM(P:Y) is a torn read; keep last good"


def test_consistent_total_with_py_is_adopted(monkeypatch):
    """D == SUM(P:Y) → trustworthy, committed."""
    m = _load_tui()
    m.STATE.today_points = 0
    raw = "758|=D|=D|=D|=D|=D|=D|=D|=D|=D|21|29|377|33|195|34|13|6|50|0"
    _run(monkeypatch, m, raw)
    assert m.STATE.today_points == 758


def test_total_trustworthy_unit():
    m = _load_tui()
    assert m._total_trustworthy(758, 758) is True
    assert m._total_trustworthy(1523, 758) is False  # disagrees with P:Y
    assert m._total_trustworthy(-46, None) is False   # negative, no P:Y
    assert m._total_trustworthy(4351, None) is False  # over cap, no P:Y
    assert m._total_trustworthy(606, None) is True    # under cap, no P:Y
    assert m._total_trustworthy(759, 758) is True     # ±1 rounding tolerance
    # Regression (2026-07-03): D and its own P:Y range torn to the SAME spike must
    # STILL be rejected — agreement above the cap isn't proof of a real total.
    # (This set the cold-start current-block header to 5064分.)
    assert m._total_trustworthy(5064, 5064) is False  # matching torn spike over cap
    assert m._total_trustworthy(2001, 2001) is False  # just over the cap, agreeing
    assert m._total_trustworthy(2000, 2000) is True   # exactly at the cap is fine


def test_current_block_running_rounds_once(monkeypatch):
    """The 287-vs-288 bug: a 217.5分 locked block. Rounding each term then
    subtracting (511 − 6 − 218 = 287) is wrong; the residual must round once
    (round(511.357 − 223.5) = 288), matching the sheet's own 午 cell. No P:Y →
    cap backstop accepts the total. Layout: D | 卯=resid | 辰=6 | 巳=217.5 | …resid."""
    m = _load_tui()
    m.STATE.block_running_pts = 0
    _run(monkeypatch, m, "511.357142857143|=D|6|217.5|=D|=D|=D|=D|=D|=D")
    assert m.STATE.today_points == 511
    assert m.STATE.block_running_pts == 288, "current-block residual must round once"


def test_torn_read_does_not_update_running_pts(monkeypatch):
    """A rejected torn read must leave the last-good current-block 分 untouched."""
    m = _load_tui()
    m.STATE.block_running_pts = 388  # last good
    _run(monkeypatch, m, "4351||3|215|=D|=D|=D|=D|=D|=D")  # spike, rejected
    assert m.STATE.block_running_pts == 388, "torn read must not poison running 分"
