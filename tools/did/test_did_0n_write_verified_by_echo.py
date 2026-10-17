#!/usr/bin/env python3
"""Regression (2026-10-17, "I still don't see anything written to neon"):
did-fast took `ix-osa exit 0` as proof a 0n write landed. While the ix-osa
helper was being edited on ix, dtd's retries got rc 0 with no verify echo,
did-fast printed "0n ✓" for seven habits, and the cells stayed empty. The
0n AppleScript already echoes "OK:row=N|<col>=<val>|..." after writing;
did-fast must require that echo to match what it wrote."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent
_SPEC = importlib.util.spec_from_file_location("did_fast_0n_verify", HERE / "did-fast.py")
_df = importlib.util.module_from_spec(_SPEC)
sys.modules["did_fast_0n_verify"] = _df
_SPEC.loader.exec_module(_df)  # type: ignore[union-attr]


class _Item:
    def __init__(self, name):
        self.name = name


class _W:
    def __init__(self, name, col, val):
        self.item, self.col_num, self.write_value = _Item(name), col, val


WRITES = [_W("push", 24, 1), _W("night hcmc", 16, 30), _W("i444", 7, 0)]
GOOD = "OK:row=291|7=0.0|14=0.0|24=1.0|16=30.0|\nPRE\t7\t\nPRE\t24\t"


def test_matching_echo_is_ok():
    assert _df.verify_0n_output(GOOD, WRITES) is None


def test_no_output_is_not_ok():
    assert "no OK:row" in _df.verify_0n_output("", WRITES)
    assert "no OK:row" in _df.verify_0n_output("date Saturday, October 17, 2026", WRITES)


def test_missing_or_wrong_column_is_not_ok():
    assert "missing" in _df.verify_0n_output("OK:row=291|7=0.0|16=30.0|", WRITES)
    assert "reads 0.0, wrote 1" in _df.verify_0n_output("OK:row=291|7=0.0|24=0.0|16=30.0|", WRITES)


def test_cumulative_habit_only_needs_presence():
    writes = [_W("xk20", 36, 20)]   # cumulative: the echo is old + new
    assert "xk20" in _df.CUMULATIVE_0N
    assert _df.verify_0n_output("OK:row=291|36=65.0|", writes) is None
    assert "missing" in _df.verify_0n_output("OK:row=291|7=0.0|", writes)


def test_output_flag_uses_the_verifier():
    src = (HERE / "did-fast.py").read_text()
    i = src.index('output["0n_write"] = {')
    assert "verify_0n_output(on_result.stdout, on_writes)" in src[i - 300:i]
    assert '"ok": on_result.returncode == 0 and on_verr is None' in src[i:i + 200]


def test_dtd_outcome_marks_unverified_0n_write_failed():
    spec = importlib.util.spec_from_file_location("dtd_outcome_t", HERE / "dtd_outcome.py")
    oc = importlib.util.module_from_spec(spec)
    sys.modules["dtd_outcome_t"] = oc
    spec.loader.exec_module(oc)
    oc._completed_today = lambda: (set(), set())
    out = {"results": [{"name": "push", "step": "0n", "value": 1}], "agent_needed": [],
           "0n_write": {"ok": False, "output": "", "error": "0n verify: no OK:row echo ('')"}}
    rows = oc.classify(out, [("6gHVjpwWrfCPCMM6", "push (2) [5]")])
    assert rows[0][1] == "failed" and "0n ✗" in rows[0][2], rows
    out["0n_write"] = {"ok": True, "output": "OK:row=291|24=1.0|"}
    assert oc.classify(out, [("6gHVjpwWrfCPCMM6", "push (2) [5]")])[0][1] == "ok"


def test_did_fast_exits_nonzero_when_a_write_did_not_land():
    assert _df._write_failures({"results": [], "0n_write": {"ok": True, "output": "OK:row=1|5=1.0|"}}) == []
    assert _df._write_failures({"0n_write": {"ok": False, "output": "", "error": "0n verify: no OK:row echo ('')"}}) \
        == ["0n_write: 0n verify: no OK:row echo ('')"]
    src = (HERE / "did-fast.py").read_text()
    i = src.index("    _emit(output)\n")
    tail = src[i:i + 500]
    assert "_write_failures(output)" in tail and "sys.exit(2)" in tail
