#!/usr/bin/env python3
"""Regression test (bug 2026-08-16): a second same-day /did completion for a
kid-time habit (xk20/xk22/xk26 — minutes with Theo/Ren/Rori) was overwriting
the day's 0n cell instead of adding to it. These habits legitimately fire
several times a day (separate play sessions), so each write must accumulate.

Root cause: xk20/xk22/xk26 were in VARIABLE_0N (duration-based value) but
missing from CUMULATIVE_0N, so build_0n_script() took the plain-overwrite
branch (`set value of cell ... to N`) instead of the read-old-add-new branch
every other cumulative habit gets.
"""
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "did_fast_kidtime", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_kidtime"] = mod
    spec.loader.exec_module(mod)
    return mod


def _route_result(mod, name, col, val):
    item = mod.ParsedItem(raw=name, name=name)
    return mod.RouteResult(item=item, step="0n", col_num=col, write_value=val)


def test_xk20_xk22_xk26_are_cumulative():
    m = _load()
    assert "xk20" in m.CUMULATIVE_0N
    assert "xk22" in m.CUMULATIVE_0N
    assert "xk26" in m.CUMULATIVE_0N


def test_build_0n_script_accumulates_xk22_instead_of_overwriting():
    m = _load()
    w = _route_result(m, "xk22", "AK", 15)
    script = m.build_0n_script([w], "8/16")
    # The accumulating branch: read the existing value first, then add to it
    # (falling back to a plain set only when the cell was previously empty).
    assert "set oldVal to value of cell AK of row todayRow of ws" in script
    assert "(oldVal as number) + 15" in script


def test_build_0n_script_still_overwrites_non_cumulative_habit():
    """Sanity check the fix is scoped correctly: an ordinary VARIABLE_0N
    habit NOT in CUMULATIVE_0N (e.g. hiit — one session a day) still gets
    the plain overwrite, not accidentally swept into accumulation."""
    m = _load()
    w = _route_result(m, "hiit", "AE", 10)
    script = m.build_0n_script([w], "8/16")
    assert "set value of cell AE of row todayRow of ws to 10" in script
    assert "oldVal" not in script
