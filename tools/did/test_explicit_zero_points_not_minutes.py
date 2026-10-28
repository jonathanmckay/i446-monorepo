#!/usr/bin/env python3
"""Regression (2026-10-28): an explicit [0] on a timed entry credited the
minutes anyway (`points_override or time_value` read 0 as "unset"), so
janus's points prompt could never record a deliberate zero."""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


@pytest.fixture()
def df():
    spec = importlib.util.spec_from_file_location("did_fast_zero", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_zero"] = mod
    spec.loader.exec_module(mod)
    return mod


def _tq():
    return {"0neon": [], "夜neon": [], "1neon": [], "today": []}


def _route(df, text):
    return df.route_items(df.parse_input(text), {"0n": {}, "1n": {}}, _tq(), skip_todoist=True)[0]


def test_explicit_zero_is_zero_not_minutes(df):
    r = _route(df, "ad hoc thing 1000-1030 @i9 [0]")
    assert r.step == "variable" and r.fen_points == 0


def test_no_points_still_falls_back_to_minutes(df):
    assert _route(df, "ad hoc thing 1000-1030 @i9").fen_points == 30
    assert _route(df, "ad hoc thing 1000-1030 @i9 [45]").fen_points == 45


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
