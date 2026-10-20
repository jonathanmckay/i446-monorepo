"""Regression (2026-09-29): _blocks_consistent rejected clean Neon reads when
the per-block ints (each rounded from a fractional cell) summed a few points
above the rounded Σ. Logged evidence: 2026-09-28 23:54-23:59, Σ=1367 with
blocks {10,449,68,214,150,189,287,3} = 1370, five reads in a row thrown
away; a janus restarted in that window rendered no block points."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_bc", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_bc"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_rounding_drift_of_a_few_points_is_consistent():
    m = _load()
    bp = {"卯": 10, "辰": 449, "巳": 68, "午": 214, "未": 150, "申": 189, "酉": 287, "戌": 3}
    assert sum(bp.values()) == 1370
    assert m._blocks_consistent(1367, bp) is True  # the logged 2026-09-28 case
    # worst case for nine blocks: 0.5 each, plus the original +2 slack
    nine = {k: 100 for k in "卯辰巳午未申酉戌亥"}
    assert m._blocks_consistent(900 - 7, nine) is True


def test_real_torn_read_is_still_rejected():
    m = _load()
    # 2026-06-12 shape: 未 read 975 on a 728分 day
    assert m._blocks_consistent(728, {"未": 975, "辰": 100}) is False
    # a modest but real overshoot beyond rounding drift still fails
    assert m._blocks_consistent(500, {"辰": 300, "巳": 220}) is False


def test_empty_blocks_and_negative_total_still_consistent():
    m = _load()
    assert m._blocks_consistent(-46, {}) is True
    assert m._blocks_consistent(0, {}) is True
