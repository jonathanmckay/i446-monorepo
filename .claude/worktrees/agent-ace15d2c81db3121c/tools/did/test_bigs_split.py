#!/usr/bin/env python3
"""Regression test: `did bigs <minutes>` splits the minutes between xk20 (Theo)
and xk22 (Ren). Pure routing test — no Excel/Todoist.

Run: python3 -m pytest test_bigs_split.py
"""
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("didfast", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_spec)
sys.modules["didfast"] = df  # needed for dataclass `int | float` hint resolution
_spec.loader.exec_module(df)

HEADERS = {"0n": {"xk20": 36, "xk22": 37}, "1n": {}}
TQ = {"0neon": [], "夜neon": [], "1neon": []}


def _route(inp):
    items = df.parse_input(inp)
    results = df.route_items(items, HEADERS, TQ, skip_todoist=True)
    return {r.col_num: r.write_value for r in results}, results


def test_bigs_even_split():
    cols, results = _route("bigs 26")
    assert all(r.step == "0n" for r in results)
    assert cols == {36: 13, 37: 13}, cols


def test_bigs_odd_minute_goes_to_xk20():
    # 31 → Theo (xk20) gets the extra minute
    cols, _ = _route("bigs 31")
    assert cols == {36: 16, 37: 15}, cols


def test_bigs_time_range_splits():
    # 14:30–15:01 = 31 min → 16 / 15
    cols, _ = _route("bigs 1430-1501")
    assert cols == {36: 16, 37: 15}, cols


def test_bigs_produces_two_writes_in_same_row():
    _, results = _route("bigs 26")
    assert len(results) == 2
    assert {r.col_num for r in results} == {36, 37}


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
