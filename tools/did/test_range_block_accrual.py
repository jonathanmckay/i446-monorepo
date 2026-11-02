#!/usr/bin/env python3
"""Accrual, not cash (JM 2026-10-28): /did of an HHMM-HHMM range credits the
地支 block the work happened in, not the block it was logged in. JM
2026-11-01: a range crossing a block boundary credits the block it ENDED in,
and a per-minute credit over 100分 is split per block (one Toggl entry and
one block credit per segment) — split_results_by_block."""
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


def test_range_spanning_blocks_takes_the_block_it_ended_in(df):
    assert _one(df, "xcore 0918-1241 @i9").block_override == "未"   # ends 12:41 → 未
    assert _one(df, "a 0930-1030 @i9").block_override == "午"       # ends 10:30 → 午
    assert _one(df, "a 0930-1000 @i9").block_override == "巳"       # last minute 09:59 → 巳


def test_typed_glyph_wins_and_is_not_marked_inferred(df):
    it = _one(df, "write memo 0830-0910 @i9 申")
    assert it.block_override == "申" and not it.block_inferred


def test_no_range_or_past_day_is_left_alone(df):
    assert _one(df, "write memo 30 @i9").block_override is None
    assert _one(df, "write memo 0830-0910 @i9 1/2").block_override is None


def test_range_block_segments(df):
    assert df.range_block_segments("0918", "1241") == [
        ("巳", "0918", "1000"), ("午", "1000", "1200"), ("未", "1200", "1241")]
    assert df.range_block_segments("0830", "0910") == [("巳", "0830", "0910")]
    assert df.range_block_segments("2200", "2345") == [("亥", "2200", "2345")]  # clamps
    assert df.range_block_segments("junk", "1000") == []


# ── split_results_by_block ────────────────────────────────────────────────

def _variable(df, text, fen_col="W"):
    it = _one(df, text)
    r = df.RouteResult(item=it, step="variable", fen_col=fen_col,
                       fen_points=df.time_range_minutes(*it.time_range))
    return r


def test_long_per_minute_variable_is_split_per_block(df):
    out = df.split_results_by_block([_variable(df, "walk 0918-1241 @hcb")])
    assert [(r.item.block_override, r.item.time_range, r.fen_points) for r in out] == [
        ("巳", ("0918", "1000"), 42), ("午", ("1000", "1200"), 120), ("未", ("1200", "1241"), 41)]
    assert sum(r.fen_points for r in out) == 203
    assert all(r.item.block_inferred and r.item.split_of == ("0918", "1241") for r in out)
    assert [r.item.split_part for r in out] == [(0, 3), (1, 3), (2, 3)]
    assert out[0].item.name == "walk" and out[2].step == "variable"


def test_under_100_points_or_single_block_is_not_split(df):
    out = df.split_results_by_block([_variable(df, "walk 0930-1100 @hcb")])   # 90分, 2 blocks
    assert len(out) == 1 and out[0].item.block_override == "午" and out[0].item.split_part is None
    out = df.split_results_by_block([_variable(df, "walk 1005-1200 @hcb")])   # 115分, 1 block
    assert len(out) == 1 and out[0].item.split_part is None


def test_explicit_points_are_not_per_minute_unless_rate(df):
    it = _one(df, "deck 0918-1241 @i9 [300]")
    r = df.RouteResult(item=it, step="todoist", fen_col="R", fen_points=300)
    assert len(df.split_results_by_block([r])) == 1
    it.rate_based = True   # --rate: the [300] came from a typed ".../m"
    out = df.split_results_by_block([r])
    assert [(r.item.block_override, r.fen_points, r.item.points_override) for r in out] == [
        ("巳", 62, 62), ("午", 177, 177), ("未", 61, 61)]
    assert sum(r.fen_points for r in out) == 300


def test_typed_glyph_blocks_the_split(df):
    out = df.split_results_by_block([_variable(df, "walk 0918-1241 @hcb 申")])
    assert len(out) == 1 and out[0].item.block_override == "申"


def test_variable_1n_split_keeps_task_on_first_segment_and_minutes_in_cell(df):
    it = _one(df, "family 1700-2030")
    r = df.RouteResult(item=it, step="1n", col_letter="AF", fen_col="X", write_value=210,
                       is_variable_1n=True, variable_value=210)
    r.todoist_task = {"id": "t1", "content": "1 family"}
    out = df.split_results_by_block([r])
    assert [(c.item.block_override, c.write_value, c.variable_value) for c in out] == [
        ("酉", 60, 60), ("戌", 120, 120), ("亥", 30, 30)]
    assert out[0].todoist_task == {"id": "t1", "content": "1 family"}
    assert out[1].todoist_task is None and out[2].todoist_task is None


def test_threshold_and_base_habits_are_not_split(df):
    for name in ("长o314", "一起饭"):
        it = _one(df, f"{name} 0918-1241")
        r = df.RouteResult(item=it, step="1n", col_letter="A", fen_col="X", write_value=203,
                           is_variable_1n=True, variable_value=203)
        assert len(df.split_results_by_block([r])) == 1, name


def test_kid_time_habit_splits_minutes_and_credits_blocks(df):
    it = _one(df, "xk22 0918-1241")
    r = df.RouteResult(item=it, step="0n", col_num=37, write_value=203)
    out = df.split_results_by_block([r])
    assert [(c.item.block_override, c.write_value) for c in out] == [("巳", 42), ("午", 120), ("未", 41)]
    # Step 5a: a point per minute via 0n!AZ → 0分!X, so the block share is the minutes.
    assert [df._kid_time_block_minutes(c) for c in out] == [42, 120, 41]
    card = df.RouteResult(item=_one(df, "math with Ren 0918-1241 @xk22"), step="todoist",
                          fen_col="0n:xk22", fen_points=0)
    assert df._kid_time_block_minutes(card) == 203
    assert df._kid_time_block_minutes(_variable(df, "walk 0918-1241 @hcb")) is None


def test_hcbi_habits_are_left_alone(df):
    r = _variable(df, "bball 0918-1241 @hcbp", fen_col="hcbi:Y")
    assert len(df.split_results_by_block([r])) == 1


def test_helpers_for_toggl_and_posthoc(df):
    out = df.split_results_by_block([_variable(df, "walk 0918-1241 @hcb")])
    assert len({df._toggl_key(r) for r in out}) == 3
    assert [df._is_later_split_part(r) for r in out] == [False, True, True]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
