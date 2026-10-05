#!/usr/bin/env python3
"""/1n backfill (2026-10-05): mark a weekly 1₦+ habit for a past week, credit
today. Pure-helper tests: week parsing (fiscal labels, dates, 'last week'),
the 0分 column lookup (incl. hyphenated headers like '1 -2g', which
header_normalize turns into '1 2g'), and the already-marked guard."""
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("on_backfill", _HERE / "1n-backfill.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["on_backfill"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ob():
    return _load()


@pytest.fixture(scope="module")
def df(ob):
    return ob._did()


TODAY = date(2026, 10, 5)  # Monday of week 10.1 (Sun 10/4)


@pytest.mark.parametrize("raw,label,sunday", [
    ("9.5", "9.5", date(2026, 9, 27)),
    ("9.4", "9.4", date(2026, 9, 20)),
    ("last week", "9.5", date(2026, 9, 27)),
    ("9/24", "9.4", date(2026, 9, 20)),
    ("2026-09-26", "9.4", date(2026, 9, 20)),
    ("yesterday", "10.1", date(2026, 10, 4)),
    ("this week", "10.1", date(2026, 10, 4)),
])
def test_parse_week(ob, raw, label, sunday):
    assert ob.parse_week(raw, TODAY) == (label, sunday)


def test_parse_week_unknown_label(ob):
    with pytest.raises(ValueError):
        ob.parse_week("13.1", TODAY)


@pytest.mark.parametrize("key,col", [
    ("1 2g", "T"),      # header '1 -2g' normalized; raw-keyed map missed it
    ("1 1n", "P"),      # header '1 -1n'
    ("1 xk88", "Y"),
    ("1 m5x2", "S"),    # generic '1 <domain>' fallback
    ("family", "X"),
    ("nails", None),
])
def test_resolve_fen_col(ob, df, key, col):
    assert ob.resolve_fen_col(df, key, None) == col


def test_resolve_fen_col_domain_override(ob, df):
    assert ob.resolve_fen_col(df, "nails", "hcb") == "W"


@pytest.mark.parametrize("formula,marked", [
    ("", False), ("0", False), ("=0+0", False),
    ("1", True), ("=0+1", True), ("69", True), ("x", True),
])
def test_is_marked(ob, formula, marked):
    assert ob.is_marked(formula) is marked


def test_parse_read(ob):
    assert ob.parse_read("ROW\t44\nCELL\t=0+1\nE\t15\n") == {
        "ROW": "44", "CELL": "=0+1", "E": "15"}


def test_did_fast_hyphenated_header_reaches_0fen(df):
    """/did routing: '1 -2g' must resolve to its 0分 column, not None."""
    item = df.ParsedItem(raw="1 -2g", name="1 -2g")
    headers = {"0n": {}, "1n": {"1 -2g": "K", "1 -1n": "M"}}
    tq = {"0neon": [], "夜neon": [], "1neon": []}
    res = df.route_items([item], headers, tq)
    one_n = [r for r in res if r.step == "1n"]
    assert one_n and one_n[0].fen_col == "T"
