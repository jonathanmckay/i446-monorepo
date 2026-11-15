"""o314 day total + 长o314 long-session bonus in did-fast (JM 2026-11-03).

o314 is 1分/min (sheet formula). A day total of 30m+ earns 0.5分 for every
minute of the day, credited to the weekly 长o314 habit; the bonus is derived
from AQ's value before and after each write (lib/neon/long_bonus.py).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
_SPEC = importlib.util.spec_from_file_location("did_fast_o314", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_SPEC)
sys.modules["did_fast_o314"] = df
_SPEC.loader.exec_module(df)  # type: ignore[union-attr]

AQ = 43  # 0n column number of o314


def _w(name="o314", **kw):
    item = df.ParsedItem(raw=name, name=name, target_date="11/3", **kw)
    return df.RouteResult(item=item, step="0n", col_num=AQ, write_value=kw.get("time_value") or 69)


def _echo(before, after):
    return f"OK:row=308|{AQ}={after}|\nPRE\t{AQ}\t{before}\n"


CARD = {"id": "c1", "content": "long o314 (30) [15]", "labels": ["1neon", "hcm"]}


def test_bare_completion_sets_day_total_session_adds():
    assert not df._adds_0n(_w())                               # Toggl day total → set
    assert df._adds_0n(_w(time_value=20))                      # typed session → add
    assert df._adds_0n(_w(time_range=("0715", "0824")))       # janus range → add
    assert df._adds_0n(_w("hcmr", time_value=10))
    assert not df._adds_0n(_w("其他人", time_value=10))        # other habits unchanged


def test_crossing_30_credits_whole_day_and_closes_weekly_card():
    out = df._long_bonus_results([_w()], _echo("", "69.0"), {"1neon": [CARD]})
    assert len(out) == 1
    r = out[0]
    assert (r.item.name, r.step, r.col_letter, r.fen_col) == ("长o314", "1n", "V", "V")
    assert r.is_variable_1n and r.variable_value == r.write_value == 34.5
    assert r.todoist_task == CARD


def test_second_session_credits_only_the_change():
    assert df._long_bonus_results([_w()], _echo("", "20"), {}) == []
    [r] = df._long_bonus_results([_w()], _echo("20", "35"), {})
    assert r.variable_value == 17.5
    [r] = df._long_bonus_results([_w()], _echo("35", "50"), {})
    assert r.variable_value == 7.5


def test_hcmr_counts_as_o314():
    [r] = df._long_bonus_results([_w("hcmr")], _echo("25", "40"), {})
    assert r.variable_value == 20


def test_resend_or_downward_correction_credits_nothing():
    assert df._long_bonus_results([_w()], _echo("69", "69"), {}) == []
    assert df._long_bonus_results([_w()], _echo("69", "40"), {}) == []


def test_other_habits_never_get_the_bonus():
    w = _w("其他人")
    assert df._long_bonus_results([w], _echo("", "60"), {}) == []


def test_meditation_day_total_earns_the_long_bonus():
    [r] = df._long_bonus_results([_w("冥想")], _echo("28", "45"), {})
    assert r.item.name == "长冥想" and r.variable_value == 22.5


def test_meditation_sessions_add_instead_of_overwriting():
    assert df._adds_0n(_w("冥想", time_range=("2001", "2018")))
    assert not df._adds_0n(_w("冥想"))                         # bare close: Toggl day total


def test_long_meditation_routes_as_meditation():
    assert df.LONG_TO_BASE_0N["长冥想"] == "冥想"


@pytest.mark.parametrize("text", ["long o314", "长o314"])
def test_long_o314_routes_as_o314(text):
    assert df.LONG_TO_BASE_0N.get(df.header_normalize(
        df.ONENEON_ALIASES.get(text.lower(), text.lower()))) == "o314"


# -- past-day writes (bug 2026-11-14) -----------------------------------------
# Yesterday's 62m o314, logged from janus's past-day view (--past-ok), wrote
# AQ=62 but no 长o314 credit: the call site gated the bonus on `not
# past_target`. The 1n path already handles past days, so the bonus must run.

import datetime as _dt
import inspect


class _Today:
    @staticmethod
    def today():
        return _dt.date(2026, 11, 14)


def test_past_day_write_still_earns_the_long_bonus(monkeypatch):
    monkeypatch.setattr(df, "_daytime", _Today)
    w = _w(); w.item.target_date = "11/13"
    [r] = df._long_bonus_results([w], _echo("", "62"), {"1neon": [CARD]}, past_target=True)
    assert (r.item.name, r.step, r.variable_value) == ("长o314", "1n", 31)
    assert r.item.target_date == "11/13"


def test_past_day_bonus_closes_card_only_in_the_current_week(monkeypatch):
    monkeypatch.setattr(df, "_daytime", _Today)
    same = _w(); same.item.target_date = "11/13"          # Fri of the running week
    [r] = df._long_bonus_results([same], _echo("", "62"), {"1neon": [CARD]}, past_target=True)
    assert r.todoist_task == CARD
    old = _w(); old.item.target_date = "11/3"             # a finished week
    assert (df.neon_weeks.habit_week_label("长o314", _dt.date(2026, 11, 3))
            != df.neon_weeks.habit_week_label("长o314", _dt.date(2026, 11, 14)))
    [r] = df._long_bonus_results([old], _echo("", "62"), {"1neon": [CARD]}, past_target=True)
    assert r.variable_value == 31 and r.todoist_task is None


def test_main_no_longer_gates_the_bonus_on_past_target():
    src = inspect.getsource(df.main)
    assert "_long_bonus_results(on_writes, on_result.stdout, tq,\n" in src or \
           "_long_bonus_results(on_writes, on_result.stdout, tq, past_target=past_target)" in src.replace("\n", "").replace(" " * 40, " ")
    gate = src[src.index("_long_bonus_results(") - 120: src.index("_long_bonus_results(")]
    assert "not past_target" not in gate
