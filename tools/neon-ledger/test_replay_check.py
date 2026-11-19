"""tools/neon-ledger/replay_check.py — replaying a journaled request must give the
cell content the daemon actually produced (Neon off Excel 4b dry-run)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("replay_check", HERE / "replay_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def _e(kind, value, before, after, **kw):
    return {"ts": "2026-11-19T08:00:00", "sheet": "0分", "col": "R", "row": 321, "kind": kind,
            "value": value, "before": before, "after": after, "src": "did x", **kw}


def test_apply_mirrors_the_daemon_setters():
    assert rc.apply("='0n'!AZ306", "append", "+25") == "='0n'!AZ306+25"      # formula append
    assert rc.apply("", "append", "+1") == "=1"                               # empty cell: "=" + N
    assert rc.apply("0", "append", "+119") == "=119"                          # "0" counts as empty
    assert rc.apply("2", "append", "+2") == "=2+2"                            # bare number → formula (2026-07-14)
    assert rc.apply("=7", "append", "-7.5") == "=7-7.5"
    assert rc.apply("", "append", ", 2 tacos") == "2 tacos"                   # text append strips the lead-in
    assert rc.apply("eggs", "append", ", toast") == "eggs, toast"
    assert rc.apply("=IF(A1,20,0)", "write", "15") == "15"                    # a write replaces the cell


def test_real_requests_reproduce():
    assert rc.classify(_e("append", "+25", "='0n'!AZ306", "='0n'!AZ306+25")) == "reproduced"
    assert rc.classify(_e("write", "1", "", "1")) == "reproduced"
    assert rc.classify(_e("write", 1, "", "1.0")) == "reproduced"             # numeric echo
    # 2026-11-09 janus: "+15.0" lands in Excel as "+15"
    assert rc.classify(_e("append", "+15.0", "='0n'!BA314+10", "='0n'!BA314+10+15")) == "reproduced"
    assert rc.classify(_e("append", "+15.0", "", "=15")) == "reproduced"
    assert rc.classify(_e("append", "+1.05", "=1", "=1+1.5")) == "mismatch"   # only a trailing .0 is dropped


def test_add_mode_is_named_not_reproduced():
    """mode "add" journals as a plain write of N with no mode: after = before + N."""
    assert rc.classify(_e("write", "15", "30", "45")) == "needs_mode"
    assert rc.classify(_e("write", "15", "", "15")) == "reproduced"           # add on an empty cell is a set
    assert rc.classify(_e("write", "15", "0", "15")) == "reproduced"
    # once the daemon journals the mode (excel-http 1.12.1) an add replays
    assert rc.classify(_e("write", "15", "30", "45", mode="add")) == "reproduced"
    assert rc.classify(_e("write", "0", "47", "47", mode="add")) == "reproduced"
    assert rc.classify(_e("write", "15", "30", "50", mode="add")) == "mismatch"


def test_lock_and_no_effect():
    assert rc.classify(_e("write", "", "=D303", "", src="block-turnover lock G (empty block)")) == "lock"
    # 2026-11-17: a journaled write that left the cell empty
    assert rc.classify(_e("write", "15", "", "")) == "no_effect"
    assert rc.classify(_e("append", "+'1n+'!G5", "", "")) == "no_effect"
    assert rc.classify(_e("write", "9", "=1+2", "=1+2+7")) == "mismatch"


def test_check_counts_chain_breaks_and_skips_fallbacks():
    es = [
        {"ts": "2026-11-19T03:50:15", "sheet": "0分", "col": "R", "row": 321, "kind": "baseline",
         "value": None, "before": None, "after": "='0n'!BK324"},
        _e("append", "+15", "='0n'!BK324", "='0n'!BK324+15", ts="2026-11-19T08:00:00"),
        # someone typed +4 into Excel between the two writes
        _e("append", "+10", "='0n'!BK324+15+4", "='0n'!BK324+15+4+10", ts="2026-11-19T09:00:00"),
        _e("append", "+5", "", "", ts="2026-11-19T09:30:00", fallback=True),
    ]
    out = rc.check(es)
    d = out["days"]["2026-11-19"]
    assert d["requests"] == 2 and d["reproduced"] == 2 and d["chain_break"] == 1 and d["fallback"] == 1
    assert out["chain_breaks"][0]["expected_before"] == "='0n'!BK324+15"
    assert out["by_sheet"]["0分"]["reproduced"] == 2
