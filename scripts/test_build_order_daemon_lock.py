"""Regression tests for build-order-daemon.py's 0分 block-lock.

2026-06-12 bug: the 06:00 fire locked 卯 (column G) at -46 because D's live
0n penalty rollups sit negative until morning habits are logged. The frozen
negative then inflated every later block's residual (=D-SUM(locked)) by the
same amount — tg-tui showed 巳 at 173分 of a 127分 day, disagreeing with the
points the user actually had. Locks must clamp negative residuals to 0 so the
transient stays in the unlocked tail and self-corrects.
"""
import ast
import importlib.util
import pathlib
import sys

SRC = pathlib.Path(__file__).parent / "build-order-daemon.py"


def _load():
    spec = importlib.util.spec_from_file_location("bod_lock", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bod_lock"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_is_parseable():
    ast.parse(SRC.read_text())


def test_lock_clamps_negative_residual_before_writing():
    """The AppleScript body must clamp v to 0 when negative, after reading the
    formula value and before writing the literal back."""
    import inspect
    mod = _load()
    src = inspect.getsource(mod.neon_lock_cell)
    read_i = src.index("set v to value of theCell")
    clamp_i = src.index('(v as number) < 0')
    write_i = src.index("set value of theCell to v")
    assert read_i < clamp_i < write_i, (
        "negative-residual clamp must sit between the read and the write"
    )
    assert "set v to 0" in src


def test_lock_columns_follow_block_convention():
    """LOCK_AT_FIRE_HOUR must lock the column of the block that just ended,
    using the 卯=04-06 convention shared with tg-tui and the 0分 sheet writer.
    A drift here silently shifts every block's points by one column."""
    mod = _load()
    for i, (branch, lo, hi) in enumerate(mod.BRANCH_HOURS):
        fire_hour = hi + 1
        assert mod.HOUR_TO_BRANCH_BLOCK[fire_hour] == branch
        assert mod.LOCK_AT_FIRE_HOUR[fire_hour] == chr(ord("G") + i)
