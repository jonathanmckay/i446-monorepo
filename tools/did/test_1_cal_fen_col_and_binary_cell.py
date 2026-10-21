#!/usr/bin/env python3
"""Regression (2026-10-21): "1 cal looks like it didn't give points … and it
should record 1 in column R since it's not dependent on time."

Two defects on one completion of "1 cal (15) [15]" from dtd with a 12-minute
timer:
  1. ONENEON_TO_0FEN had no "1 cal" entry and the generic domain fallback
     (LABEL_TO_0FEN["cal"]) is not a domain, so fen_col stayed None and the
     15 points never reached 0分 (same class as the 1 hcme / 1 m5x2 fixes).
     The Todoist card carries the g245 label → column T.
  2. The 1n+ week cell took the timer's minutes (12). Only VARIABLE
     (per-minute) habits record minutes; a standard habit's cell is a 1.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("did_fast_cal", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_cal"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def df():
    return _load()


HEADERS = {"0n": {}, "1n": {"1 cal": "R", "family": "K"}}


def _tq():
    return {"0neon": [], "夜neon": [], "1neon": [], "today": []}


def test_1_cal_resolves_the_g245_fen_col(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 12)
    r = df.route_items(df.parse_input("1 cal"), HEADERS, _tq(), skip_todoist=True)[0]
    assert r.step == "1n"
    assert r.fen_col == "T", "1 cal is labelled g245 in Todoist → 0分 column T"


def test_standard_1n_cell_is_1_even_with_minutes(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 12)
    r = df.route_items(df.parse_input("1 cal 12"), HEADERS, _tq(), skip_todoist=True)[0]
    assert r.step == "1n" and not r.is_variable_1n
    assert r.write_value == 1, "a standard weekly habit is not time-dependent: the week cell is a 1"
    # explicit minutes keep flowing for a VARIABLE habit (per-minute points)
    v = df.route_items(df.parse_input("family 45"), HEADERS, _tq(), skip_todoist=True)[0]
    assert v.is_variable_1n and v.write_value == 45


def test_unmapped_1n_header_warns(df, monkeypatch, capsys):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 5)
    r = df.route_items(df.parse_input("1 zzz"), {"0n": {}, "1n": {"1 zzz": "Q"}}, _tq(), skip_todoist=True)[0]
    assert r.step == "1n" and r.fen_col is None
    assert "no 0分 column" in capsys.readouterr().err


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
