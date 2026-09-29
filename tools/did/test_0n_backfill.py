#!/usr/bin/env python3
"""/0n (2026-09-21): mark a 0₦ habit on a past date, move its points to today.
Pure-helper tests: date parsing, formula-append number hygiene, delta
measurement and the Σdelta == weight×value guard (with the 0g +9 exception),
and the AppleScript output parser."""
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("zn_backfill", _HERE / "0n-backfill.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zn_backfill"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def zb():
    return _load()


TODAY = date(2026, 9, 21)


@pytest.mark.parametrize("raw,expect", [
    ("09.18", date(2026, 9, 18)), ("9/18", date(2026, 9, 18)), ("9.18", date(2026, 9, 18)),
    ("2026-09-18", date(2026, 9, 18)), ("yesterday", date(2026, 9, 20)),
    ("12/31", date(2025, 12, 31)),  # M/D after today → last year
])
def test_parse_target_date(zb, raw, expect):
    assert zb.parse_target_date(raw, TODAY) == expect


def test_col_letter(zb):
    assert [zb.col_letter(n) for n in (1, 19, 26, 27, 32, 58)] == ["A", "S", "Z", "AA", "AF", "BF"]


def test_number_hygiene(zb):
    assert zb.fmt_num(26.0) == "26"
    assert zb.fmt_num(7.5) == "7.5"
    assert zb.signed(20) == "+20" and zb.signed(-20) == "-20"
    assert zb.signed(-(-9)) == "+9"  # no '--9'
    assert zb.parse_num("missing value") == 0.0 and zb.parse_num("") == 0.0
    with pytest.raises(ValueError):
        zb.parse_num("#REF!")


def test_deltas_and_guard(zb):
    pre = {c: 0.0 for c in zb.FEN_COLS}
    pre[20] = 11.0
    post = dict(pre)
    post[20] = 31.0  # 0l (weight 20) landed on 个
    d = zb.compute_deltas(pre, post)
    assert d == {20: 20.0}
    assert zb.deltas_match_expected(d, 20, habit_col=19)
    # 0g: +8 on 个 and +9 on 0g column is the one allowed extra
    post2 = dict(pre); post2[20] = 19.0; post2[17] = 9.0
    d2 = zb.compute_deltas(pre, post2)
    assert zb.deltas_match_expected(d2, 8, habit_col=20)
    assert not zb.deltas_match_expected(d2, 8, habit_col=19)
    # something else moved (e.g. the 111 all-colors bonus) → refuse
    post3 = dict(post); post3[20] = 142.0
    assert not zb.deltas_match_expected(zb.compute_deltas(pre, post3), 20, habit_col=19)


def test_fen_col_for_habit(zb):
    assert zb.fen_col_for_habit(19) == 20   # 0l → 个
    assert zb.fen_col_for_habit(16) == 21   # night hcmc → 媒
    assert zb.fen_col_for_habit(28) == 19   # ibx m5x2 → m5
    assert zb.fen_col_for_habit(21) == 18   # stats i9 → i9


def test_parse_script_output(zb):
    out = "ROWS\t262\t259\nWEIGHT\t20.0\nPRE0N\t\nPRE\t16\t7.0\nPRE\t20\t11.0\nPOST0N\t1.0\nPOST\t16\t7.0\nPOST\t20\t31.0\n"
    r = zb.parse_script_output(out)
    assert r["n_row"] == 262 and r["f_row"] == 259
    assert r["WEIGHT"] == "20.0" and r["PRE0N"] == "" and r["POST0N"] == "1.0"
    assert zb.compute_deltas(r["PRE"], r["POST"]) == {20: 20.0}


def test_week_start_sunday_anchored(zb):
    assert zb.week_start(date(2026, 9, 21)) == date(2026, 9, 20)  # Mon → Sun
    assert zb.week_start(date(2026, 9, 20)) == date(2026, 9, 20)  # Sun stays
    assert zb.week_start(date(2026, 9, 18)) == date(2026, 9, 13)


def test_script_never_touches_n_color_or_sigma(zb):
    s = zb.build_script(19, 9, 18, 1, do_write=True)
    assert "cell 32 of row nRow" not in s          # 0l time stamp (AF) never written
    assert "cell 19 of row nRow of ws to 1" in s   # the habit mark
    assert "calculate" in s
    assert "cell 4 of row fRow" not in s           # 0分!D (Σ) not read/written
    assert s.count("\"PRE\" &") == 11 and s.count("\"POST\" &") == 11
    assert "set value of cell" not in zb.build_script(19, 9, 18, 1, do_write=False)
