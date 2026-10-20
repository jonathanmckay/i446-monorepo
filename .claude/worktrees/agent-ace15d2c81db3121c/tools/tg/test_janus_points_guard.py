"""2026-06-12 bug: a 0分 row sampled mid-write (daemon lock / did-fast append
in flight) returned 未=975 on a 728分 day; fetch_points accepted it and the
phantom value stuck on screen. While any residual formula is live in G:O,
sum(blocks) == Σ exactly, so sum > Σ identifies a torn snapshot to reject."""
import importlib.util
import inspect
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_guard", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_guard"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_rejects_block_sum_exceeding_total():
    mod = _load_tui()
    bad = {"辰": 65, "巳": 290, "午": 178, "未": 975}  # the observed torn read
    assert not mod._blocks_consistent(728, bad)


def test_accepts_consistent_residual_read():
    mod = _load_tui()
    good = {"辰": 65, "巳": 290, "午": 178, "未": 195}
    assert mod._blocks_consistent(728, good)


def test_accepts_late_night_total_above_locked_sum():
    """After the 22:00 lock all blocks are literals; Σ keeps growing past
    their sum. That direction must NOT be rejected."""
    mod = _load_tui()
    locked = {"辰": 65, "巳": 290, "午": 178, "未": 195}  # sum 728
    assert mod._blocks_consistent(810, locked)


def test_fetch_points_guards_block_assignment():
    """Structural: fetch_points must consult _blocks_consistent before
    overwriting STATE.block_points."""
    mod = _load_tui()
    src = inspect.getsource(mod.fetch_points)
    guard_i = src.index("_blocks_consistent")
    assign_i = src.index("STATE.block_points = bp_excel")
    assert guard_i < assign_i, "consistency guard must gate the assignment"


def test_rejects_implausible_block_spike():
    """2026-06-30 bug: a torn read spiked the residual block 辰 to 4227 on a
    429分 day; _blocks_consistent passed (the residual makes sum==Σ), so the
    spike stuck on screen. A single block above _MAX_PLAUSIBLE_TOTAL is
    impossible — reject the whole read."""
    mod = _load_tui()
    assert not mod._blocks_plausible({"卯": 33, "辰": 4227})
    assert mod._blocks_plausible({"卯": 33, "辰": 396})  # the real residual
    assert mod._blocks_plausible({}), "an empty read is plausible"


def test_fetch_points_guards_against_block_spike():
    """Structural: the plausibility cap must also gate the block assignment, so a
    torn residual can't sit in block_points even when sum==Σ slips past."""
    mod = _load_tui()
    src = inspect.getsource(mod.fetch_points)
    assert "_blocks_plausible" in src, "fetch_points must apply the per-block cap"
    guard_i = src.index("_blocks_plausible")
    assign_i = src.index("STATE.block_points = bp_excel")
    assert guard_i < assign_i, "plausibility cap must gate the assignment"
