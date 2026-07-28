"""Regression tests for neon-ledger-audit replay logic (no Excel, no daemon)."""

import importlib.util
import os

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location(
    "neon_ledger_audit", os.path.join(HERE, "neon-ledger-audit.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def E(**kw):
    base = {"ts": "2026-07-28T10:00:00", "kind": "append", "sheet": "0分",
            "col": "R", "row": 207, "date": "7/28", "value": "+10",
            "before": "=1", "after": "=1+10", "src": "test"}
    base.update(kw)
    return base


def test_replay_clean_chain():
    errors, warns, last = audit.replay([
        E(before="=1", after="=1+10"),
        E(before="=1+10", after="=1+10+5"),
    ])
    assert errors == [] and warns == []
    assert last[("0分", "R", "7/28")][1] == "=1+10+5"


def test_replay_detects_break():
    errors, _, _ = audit.replay([
        E(before="=1", after="=1+10"),
        E(before="=999", after="=999+5"),  # someone edited the cell in between
    ])
    assert len(errors) == 1 and "chain BREAK" in errors[0]


def test_ack_resets_baseline():
    errors, _, _ = audit.replay([
        E(before="=1", after="=1+10"),
        E(kind="ack", value=None, before="=1+10", after="=999", note="hand fix"),
        E(before="=999", after="=999+5"),
    ])
    assert errors == []


def test_fallback_warns_not_errors():
    errors, warns, last = audit.replay([
        E(before="=1", after="=1+10"),
        E(before=None, after="=1+10+5", fallback=True),
        E(before="=1+10+5", after="=1+10+5+2"),
    ])
    assert errors == [] and len(warns) == 1 and "fallback" in warns[0]


def test_entry_key_row_fallback():
    assert audit.entry_key({"sheet": "1n+", "col": "AI", "row": 35, "date": None}) == ("1n+", "AI", "r35")


def test_block_glyph():
    assert audit.block_glyph("2026-07-28T10:15:00") == "午"
    assert audit.block_glyph("2026-07-28T04:00:00") == "卯"
    assert audit.block_glyph("2026-07-28T23:30:00") == "亥"
    assert audit.block_glyph("2026-07-28T02:00:00") == "卯"


def test_later_ack_downgrades_earlier_break_to_warning():
    errors, warns, _ = audit.replay([
        E(before="=1", after="=1+10"),
        E(before="=999", after="=999+5"),  # break
        E(kind="ack", value=None, before="=999+5", after="=999+5", note="explained"),
    ])
    assert errors == []
    assert any("chain BREAK" in w and "acked" in w for w in warns)


def test_unacked_break_still_errors():
    errors, _, _ = audit.replay([
        E(before="=1", after="=1+10"),
        E(before="=999", after="=999+5"),
    ])
    assert len(errors) == 1
