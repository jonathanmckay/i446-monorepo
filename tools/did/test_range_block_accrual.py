#!/usr/bin/env python3
"""Accrual, not cash (JM 2026-10-28): /did of an HHMM-HHMM range credits the
地支 block the work happened in, not the block it was logged in."""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


@pytest.fixture()
def df():
    spec = importlib.util.spec_from_file_location("did_fast_accrual", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_accrual"] = mod
    spec.loader.exec_module(mod)
    return mod


def _one(df, text):
    items = df.parse_input(text)
    df.infer_range_blocks(items)
    return items[0]


def test_range_gets_the_block_it_happened_in(df):
    it = _one(df, "write memo 0830-0910 @i9")      # 巳 = 08:00-10:00
    assert it.block_override == "巳" and it.block_inferred


def test_range_spanning_blocks_takes_the_majority_block(df):
    assert _one(df, "xcore 0918-1241 @i9").block_override == "午"   # 42m 巳, 120m 午, 41m 未
    assert _one(df, "a 0930-1030 @i9").block_override == "巳"       # 30/30 tie → earlier


def test_typed_glyph_wins_and_is_not_marked_inferred(df):
    it = _one(df, "write memo 0830-0910 @i9 申")
    assert it.block_override == "申" and not it.block_inferred


def test_no_range_or_past_day_is_left_alone(df):
    assert _one(df, "write memo 30 @i9").block_override is None
    assert _one(df, "write memo 0830-0910 @i9 1/2").block_override is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
