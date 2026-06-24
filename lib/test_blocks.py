"""Tests for the shared Earthly-Branch block gate (lib/blocks.py).

is_future_block is the single source of truth for "has this block started yet?"
— the gate that shipped wrong inline in two ritual-completion readers before
being centralized here."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("blocks", HERE / "blocks.py")
blocks = importlib.util.module_from_spec(spec)
sys.modules["blocks"] = blocks
spec.loader.exec_module(blocks)


def _now(hour, minute=0):
    return dt.datetime(2026, 6, 24, hour, minute)


def test_future_block_is_strictly_after_current_hour():
    now = _now(11, 30)  # current block is 午 (10-11)
    assert blocks.is_future_block(12, now) is True   # 未 12-13: future
    assert blocks.is_future_block(20, now) is True   # 亥 20-21: future


def test_in_progress_block_is_not_future():
    now = _now(11, 30)
    # 午 starts at 10, 11:30 is inside it — in progress, not future.
    assert blocks.is_future_block(10, now) is False


def test_block_is_not_future_the_moment_its_hour_arrives():
    now = _now(10, 0)  # 10:00 sharp
    assert blocks.is_future_block(10, now) is False  # 午 just started
    assert blocks.is_future_block(12, now) is True   # 未 still ahead


def test_past_block_is_not_future():
    now = _now(15, 0)  # 申 (14-15)
    assert blocks.is_future_block(4, now) is False   # 卯 long past
    assert blocks.is_future_block(8, now) is False   # 巳 past


def test_schedule_and_start_map_agree():
    assert blocks.BLOCK_START["卯"] == 4
    assert blocks.BLOCK_START["子"] == 22
    assert len(blocks.BLOCK_SCHEDULE) == 12  # full 12-branch day
    assert blocks.BLOCK_START == {b: h for b, h in blocks.BLOCK_SCHEDULE}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
