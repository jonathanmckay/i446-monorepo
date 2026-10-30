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
    assert last[("0分", "R", "r207")][1] == "=1+10+5"


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


def test_report_stays_small_with_thousands_of_standing_errors(tmp_path, monkeypatch):
    """2026-10-11: 48 hourly runs x ~3,000 repeated errors = 3.8 MB, which
    froze Obsidian on every rewrite. Older runs collapse to their header."""
    rep = tmp_path / "audit.md"
    monkeypatch.setattr(audit, "REPORT", str(rep))
    errs = [f"chain BREAK number {i} " + "x" * 80 for i in range(3000)]
    for _ in range(60):
        audit.write_report(errs, [], 2979)
    text = rep.read_text()
    assert len(text) < 60_000
    assert text.count("\n## ") == 48
    assert text.count("- ❌") == audit.MAX_ITEMS
    assert "2800 more" in text
    assert "3000 errors, 0 warnings" in text.split("\n## ")[-1]


def test_same_cell_by_date_and_row_is_one_chain():
    """2026-10-11: block-turnover writes 0分!P by date, ritual -1n by row; the
    daemon logs the resolved row on both, so they must share one chain."""
    entries = [
        {"ts": "1", "kind": "write", "sheet": "0分", "col": "P", "date": "10/11", "row": 282, "before": "=0", "after": "=3"},
        {"ts": "2", "kind": "write", "sheet": "0分", "col": "P", "date": None, "row": 282, "before": "=3", "after": "=3+13"},
        {"ts": "3", "kind": "write", "sheet": "0分", "col": "P", "date": "10/11", "row": 282, "before": "=3+13", "after": "=3+13+13"},
    ]
    errors, _, _ = audit.replay(entries)
    assert errors == []


# ── 2026-10-29: strip-bug residue + replay restores (Dream v172 triage) ──────

def test_unshift_new_format_blank_cell_write():
    # d6d2f694 bug: blank-cell write echoed "\t\t1.0\t1", strip() ate the tabs
    bugged = E(ts="2026-10-20T08:14:28", kind="write", sheet="0n", col="AA",
               row=294, value="1", before="1.0", before_value="1",
               after="", after_value="")
    fixed = audit.unshift(bugged)
    assert fixed["before"] == "" and fixed["after"] == "1" and fixed["unshifted"]


def test_unshift_old_format_without_before_value():
    bugged = E(ts="2026-09-06T06:57:26", kind="write", sheet="0n", col="AP",
               row=250, value="3", before="3.0", after="", after_value="3")
    assert audit.unshift(bugged)["after"] == "3"


def test_unshift_leaves_real_clears_and_post_fix_entries():
    lock = E(ts="2026-09-01T06:00:09", kind="write", col="G", value="",
             before="=D242", after="", after_value="")
    assert audit.unshift(lock) is lock
    late = E(ts="2026-10-25T08:00:00", kind="write", value="1", before="1.0",
             before_value="1", after="", after_value="")
    assert audit.unshift(late) is late


def test_strip_bug_pair_no_longer_breaks():
    errors, _, last = audit.replay([
        E(ts="2026-10-20T03:50:00", kind="baseline", sheet="0n", col="AA", row=294,
          before=None, after=""),
        E(ts="2026-10-20T08:14:28", kind="write", sheet="0n", col="AA", row=294,
          value="1", before="1.0", before_value="1", after="", after_value=""),
        E(ts="2026-10-20T08:15:09", kind="write", sheet="0n", col="AA", row=294,
          value="1", before="1", before_value="1.0", after="1", after_value="1.0"),
    ])
    assert errors == []
    assert last[("0n", "AA", "r294")][1] == "1"


def test_replay_restore_is_warning_not_error():
    errors, warns, _ = audit.replay([
        E(kind="write", sheet="0n", col="G", row=302, value="20", before="", after="20"),
        E(kind="write", sheet="0n", col="G", row=302, date=None, value="20",
          before="", after="20", src="replay 10-28 revert: did i444"),
    ])
    assert errors == [] and len(warns) == 1 and "restored by replay" in warns[0]


def test_replay_that_changes_value_still_breaks():
    errors, _, _ = audit.replay([
        E(kind="write", value="20", before="", after="20"),
        E(kind="write", value="25", before="", after="25", src="replay x"),
    ])
    assert len(errors) == 1


def test_reconcile_before_is_not_chain_checked_but_after_advances():
    errors, _, last = audit.replay([
        E(kind="write", value="1", before="", after="1"),
        # daemon copied a stale ledger after into before; Excel read is after
        E(kind="reconcile", value=None, before="", after="=1+5"),
        E(kind="append", value="+2", before="=1+5", after="=1+5+2"),
    ])
    assert errors == []
    assert last[("0分", "R", "r207")][1] == "=1+5+2"
