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

Fix: do the row scan SERVER-SIDE in one AppleScript call via ix-osa.sh (the
same pattern 0t-fast.py's fallback template already uses for its own
date-row scan), so finding the row costs exactly one round trip regardless
of how many rows are scanned.
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


def test_calc_mw_makes_exactly_one_subprocess_call():
    """Behavioral: whatever _calc_mw does to find the row, it must cost
    exactly one subprocess/network round trip, not one per candidate row."""
    calls = []

    class FakeCompleted:
        returncode = 0
        stdout = "27\n"
        stderr = ""

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeCompleted()

    with patch.object(run.subprocess, "run", side_effect=fake_run):
        mw, row = run._calc_mw("8/3")

    assert len(calls) == 1, f"_calc_mw made {len(calls)} subprocess calls, expected 1"
    assert row == 27
    assert isinstance(mw, float)


def test_calc_mw_raises_when_week_not_found():
    class FakeCompleted:
        returncode = 0
        stdout = "0\n"
        stderr = ""

    with patch.object(run.subprocess, "run", return_value=FakeCompleted()):
        try:
            run._calc_mw("8/3")
            assert False, "expected RuntimeError when the week row isn't found"
        except RuntimeError as e:
            assert "not found" in str(e)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
