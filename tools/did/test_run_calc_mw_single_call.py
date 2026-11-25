#!/usr/bin/env python3
"""Regression (2026-08-09): "/1s just hangs" when finishing the survey.

`run.py`'s `_calc_mw()` finds the 1n+ sheet row for a given M.W week by
scanning column B -- but it used to do that with a CLIENT-SIDE loop calling
`excel.read("1n+", "B", row=r)` once per row, for rows 4-59 (up to 56
separate network round trips). Each call is individually timeout-bounded
(20s daemon + 45s ssh fallback = up to 65s worst case), but that's additive
across the loop: if the excel-http daemon is down, one _calc_mw() call could
take tens of minutes. This runs right after 1s-survey.py's own Neon write,
in the /did mark-done chain (`run.py "1s"` -> 1n+ step -> _calc_mw), which is
exactly what made /1s look hung after the user finished answering the
survey questions -- the confirmation message (`✓ 1s saved to Neon...`)
already existed in 1s-survey.py but was never reached because execution was
still stuck inside this scan.

Fix (2026-08-09): do the row scan in one call. First one AppleScript through
ix-osa.sh; since 2026-11-25 one excel-http /lookup (excel.lookup_row), with no
raw AppleScript and no subprocess of its own.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

_RUN_SPEC = importlib.util.spec_from_file_location("did_run_calc_mw", _HERE / "run.py")
run = importlib.util.module_from_spec(_RUN_SPEC)
sys.modules["did_run_calc_mw"] = run
_RUN_SPEC.loader.exec_module(run)  # type: ignore[union-attr]

SRC = (_HERE / "run.py").read_text()


def _calc_mw_source() -> str:
    i = SRC.index("def _calc_mw(")
    j = SRC.index("\ndef ", i + 1)
    return SRC[i:j]


def test_calc_mw_no_longer_loops_over_excel_read():
    """Structural: the client-side per-row excel.read() scan must be gone.
    (The fix's own comment mentions "excel.read" by name to explain what it
    replaced, so strip comments before searching -- otherwise this check
    trivially fails against its own docstring.)"""
    body = "\n".join(line.split("#", 1)[0] for line in _calc_mw_source().splitlines())
    assert "excel.read(" not in body, (
        "_calc_mw still calls excel.read() in a loop -- the up-to-56-round-trip "
        "scan (2026-08-09 hang bug) is back")
    assert re.search(r"for r in range\(", body) is None, (
        "_calc_mw still has a client-side row-scanning loop")


def _no_subprocess(*a, **k):
    raise AssertionError("_calc_mw spawned a subprocess (raw osascript is gone)")


def test_calc_mw_makes_exactly_one_daemon_lookup_and_no_subprocess():
    """Behavioral: finding the row costs exactly one excel-http round trip
    (lookup_row), not one per candidate row, and no osascript."""
    calls = []

    def fake_lookup(sheet, label):
        calls.append((sheet, label))
        return 27

    with patch.object(run.excel, "lookup_row", side_effect=fake_lookup), \
         patch.object(run.subprocess, "run", side_effect=_no_subprocess):
        mw, row = run._calc_mw("8/3")

    assert len(calls) == 1, f"_calc_mw made {len(calls)} lookups, expected 1"
    sheet, label = calls[0]
    assert sheet == "1n+"
    # the label goes as the text col B holds ("8.1"), never a float
    assert isinstance(label, str) and re.fullmatch(r"\d{1,2}\.[1-5]", label), label
    assert row == 27 and type(row) is int
    assert isinstance(mw, float) and f"{mw}" == label


def _raises_not_found(**patches):
    with patch.object(run.excel, "lookup_row", **patches), \
         patch.object(run.subprocess, "run", side_effect=_no_subprocess):
        try:
            run._calc_mw("8/3")
        except RuntimeError as e:
            return str(e)
    raise AssertionError("expected RuntimeError")


def test_calc_mw_raises_when_week_not_found():
    msg = _raises_not_found(return_value=None)      # label absent, or daemon unreachable
    assert "not found in 1n+ col B" in msg and msg.startswith("M.W=")


def test_calc_mw_daemon_exception_takes_the_not_found_path():
    assert "not found" in _raises_not_found(side_effect=OSError("ssh died"))


def test_calc_mw_rejects_rows_outside_the_week_ladder():
    """The old scan only looked at rows 4-59; a hit on a header row or far
    below the ladder must not become a write target."""
    for bad in (0, 2, 3, 60, 400, "x", True):
        assert "not found" in _raises_not_found(return_value=bad), bad
    with patch.object(run.excel, "lookup_row", return_value=59):
        assert run._calc_mw("8/3")[1] == 59
    with patch.object(run.excel, "lookup_row", return_value=4):
        assert run._calc_mw("8/3")[1] == 4


def test_calc_mw_has_no_raw_applescript():
    body = _calc_mw_source()
    assert "tell application" not in body and "IX_OSA" not in body and "subprocess" not in body
