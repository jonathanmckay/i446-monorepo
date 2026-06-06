"""Regression tests for tg-tui per-block points.

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
    spec = importlib.util.spec_from_file_location("tg_tui_pts", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_pts"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeProc:
    def __init__(self, out, rc=0):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def test_block_points_read_from_neon_g_to_o_columns(monkeypatch):
    m = _load_tui()
    # Σ=100, then 卯..亥: 卯 empty, 辰=3, 巳=247, 午=164.57…, 未=237, 申=250, rest empty
    out = "100||3.0|247.0|164.571428571429|237.0|250.0|||"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(out))
    m.STATE.block_points = {}
    m.fetch_points()
    assert m.STATE.today_points == 100
    assert m.STATE.block_points == {
        "辰": 3, "巳": 247, "午": 165, "未": 237, "申": 250,
    }, "per-block points must come from 0分 G:O, not logging timestamps"


def test_block_points_fallback_to_timestamps_when_excel_unreachable(monkeypatch, tmp_path):
    m = _load_tui()

    def _boom(*a, **k):
        raise RuntimeError("ix unreachable")

    monkeypatch.setattr(subprocess, "run", _boom)
    # Craft a completed-today.json under a fake home
    ct_dir = tmp_path / "vault/z_ibx"
    ct_dir.mkdir(parents=True)
    today = dtm.datetime.now().strftime("%Y-%m-%d")
    (ct_dir / "completed-today.json").write_text(json.dumps({
        "date": today,
        "names": ["thing"],
        "points": {"thing": 10},
        "timestamps": {"thing": "08:30"},  # 8am → 巳 block
    }))
    monkeypatch.setattr(m.Path, "home", classmethod(lambda cls: tmp_path))
    m.STATE.block_points = {}
    m.fetch_points()
    assert m.STATE.block_points == {"巳": 10}
