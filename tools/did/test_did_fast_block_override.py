#!/usr/bin/env python3
"""Regression: /did block-override (2026-09-16 feature).

A completion can name an explicit 地支 block as a bare trailing token (e.g.
"1 kids nature 巳") to credit that block's 0分!G:O cell directly instead of
letting the points land wherever the still-live "current block" formula
happens to be. Default (no glyph) behavior is unchanged — G:O's current
block is a live "=D-SUM(locked)" formula, so an ordinary point write already
lands there for free; see resolve_block_credit()'s docstring for the
locked-block math (why crediting an earlier block doesn't also inflate the
current one).

Run: python3 -m pytest tools/did/test_did_fast_block_override.py -v
"""
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

DID_FAST = Path(__file__).parent / "did-fast.py"


def _load_did_fast():
    spec = importlib.util.spec_from_file_location("did_fast_under_test", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses' field-type resolution looks itself up via
    # sys.modules[cls.__module__] -- must be registered before exec_module
    # runs the module's @dataclass definitions.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


did_fast = _load_did_fast()


# ---------------------------------------------------------------------------
# parse_input(): glyph extraction
# ---------------------------------------------------------------------------

def test_trailing_glyph_becomes_block_override():
    items = did_fast.parse_input("1 kids nature 巳")
    assert len(items) == 1
    assert items[0].block_override == "巳"
    assert items[0].name == "1 kids nature"


def test_no_glyph_leaves_block_override_none():
    items = did_fast.parse_input("1 kids nature")
    assert items[0].block_override is None
    assert items[0].name == "1 kids nature"


def test_glyph_does_not_swallow_two_character_word():
    # 睡觉 (sleep) — neither character is a branch glyph, and even if one
    # were, the token is 2 characters long, not a standalone single-char
    # glyph, so it must never be treated as a block override.
    items = did_fast.parse_input("睡觉")
    assert items[0].block_override is None
    assert items[0].name == "睡觉"


def test_jia_is_not_a_block_glyph():
    # 家 (home domain) is a real single CJK character that could be
    # confused for a branch glyph by a careless membership check — it is
    # NOT one of 卯辰巳午未申酉戌亥 and must not be stripped.
    items = did_fast.parse_input("family time 家")
    assert items[0].block_override is None
    assert items[0].name == "family time 家"


def test_glyph_extracted_before_trailing_time_value():
    # The glyph-strip must run before the trailing-number-as-time-value
    # step, or "45 巳" would either get read as one malformed token or the
    # glyph would ride into the name.
    items = did_fast.parse_input("1 kids nature 45 巳")
    assert items[0].block_override == "巳"
    assert items[0].time_value == 45
    assert items[0].name == "1 kids nature"


def test_glyph_coexists_with_curly_points():
    items = did_fast.parse_input("eat healthyish {10} 未")
    assert items[0].block_override == "未"
    assert items[0].curly_points == 10
    assert items[0].name == "eat healthyish"


def test_each_branch_glyph_recognized():
    for g in "卯辰巳午未申酉戌亥":
        items = did_fast.parse_input(f"task {g}")
        assert items[0].block_override == g, f"glyph {g} not recognized"
        assert items[0].name == "task"


# ---------------------------------------------------------------------------
# resolve_block_credit(): current / locked / future branching
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 16, 10, 30)  # 10:30am local -> current block is 午


def test_current_block_is_skipped():
    # Already covered by the live "=D-SUM(locked)" residual formula --
    # writing anything extra here would double-count.
    action, detail = did_fast.resolve_block_credit("午", "午", _NOW)
    assert action == "skip"
    assert detail is None


def test_earlier_locked_block_is_credited_to_its_own_column():
    action, detail = did_fast.resolve_block_credit("巳", "午", _NOW)
    assert action == "credit"
    assert detail == "I"  # 0分!I = 巳, per config/neon-cols.json


def test_future_block_is_rejected_not_silently_applied():
    action, detail = did_fast.resolve_block_credit("戌", "午", _NOW)
    assert action == "future"
    assert "戌" in detail


def test_every_non_current_block_resolves_to_its_documented_column():
    expected = {"卯": "G", "辰": "H", "巳": "I", "午": "J", "未": "K",
                "申": "L", "酉": "M", "戌": "N", "亥": "O"}
    # Freeze "now" at 亥 (20:xx) so every earlier block in the loop is
    # resolvable as "credit", not "future".
    now = datetime(2026, 9, 16, 20, 30)
    for block, col in expected.items():
        if block == "亥":
            continue  # current block itself -> "skip", covered separately
        action, detail = did_fast.resolve_block_credit(block, "亥", now)
        assert action == "credit", f"{block} should be creditable by 亥"
        assert detail == col, f"{block} should map to column {col}, got {detail}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
