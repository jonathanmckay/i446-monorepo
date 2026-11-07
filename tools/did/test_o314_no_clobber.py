"""A bare /did o314 must never LOWER AQ (bug 2026-11-04, Dream v178 review).

AQ holds the day's o314+hcmr minutes. A bare completion writes today's Toggl
total; when Toggl is unreachable (or has no matching entry) that fell back to
SET 1, wiping e.g. 70 (-69分), and a typed session was lost when a later bare
completion set AQ to a smaller Toggl total. Bare writes now use mode "max".
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1] / "lib"))
import janus_done as jd  # noqa: E402


@pytest.fixture()
def df():
    spec = importlib.util.spec_from_file_location("did_fast_no_clobber", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_no_clobber"] = mod
    spec.loader.exec_module(mod)
    yield mod
    mod._TOGGL_TODAY = None


HEADERS = {"0n": {"o314": "AQ", "冥想": "AB"}, "1n": {}}
TQ = {"0neon": [], "夜neon": [], "1neon": [], "today": []}


def _route_one(df, text):
    [r] = df.route_items(df.parse_input(text), HEADERS, TQ, skip_todoist=True)
    return r


@pytest.mark.parametrize("name", ["o314", "hcmr"])
def test_bare_completion_with_toggl_is_max(df, monkeypatch, name):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda n: 40)
    r = _route_one(df, name)
    assert (r.write_value, df._0n_mode(r)) == (40, "max")


def test_bare_completion_toggl_down_is_max_1_not_set_1(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda n: None)
    r = _route_one(df, "o314")
    assert (r.write_value, df._0n_mode(r)) == (1, "max")


def test_typed_session_still_adds_and_other_habits_still_set(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda n: 40)
    assert df._0n_mode(_route_one(df, "o314 20")) == "add"
    assert df._0n_mode(_route_one(df, "冥想")) == "set"


def test_daemon_body_carries_max(df, monkeypatch):
    seen = {}

    def fake(sheet, writes, *, date=None, row=None, src=None, fallback=True):
        seen["writes"] = writes
        return {"ok": True, "row": 308, "results": [
            {"ok": True, "col": "AQ", "value": "70", "before_value": "70", "mode": "max"}]}

    monkeypatch.setattr(df.neon_excel, "batch_write", fake)
    w = df.RouteResult(item=SimpleNamespace(name="o314"), step="0n", col_num=43,
                       write_value=1, write_mode="max")
    res = df.write_0n_via_daemon([w], "11/4")
    assert seen["writes"] == [{"col": "AQ", "value": 1, "mode": "max"}]
    # verify must accept a max write that left a larger value in place
    assert df.verify_0n_output(res.stdout, [w]) is None


def test_raw_applescript_fallback_never_lowers(df):
    w = df.RouteResult(item=SimpleNamespace(name="o314"), step="0n", col_num=43,
                       write_value=1, write_mode="max")
    script = df.build_0n_script([w], "11/4")
    assert "else if (oldVal as number) < 1 then" in script
    assert "set value of cell 43 of row todayRow of ws to 1\n    end if" in script


def test_timer_stop_minutes_turn_a_bare_miss_into_a_session(df):
    item = df.ParsedItem(raw="o314", name="o314", target_date="11/4")
    r = df.RouteResult(item=item, step="0n", col_num=43, write_value=1, write_mode="max")
    df.apply_timer_minutes([r], {"minutes": 25, "description": "o314"})
    assert (r.write_value, df._0n_mode(r)) == (25, "add")


def test_credited_is_the_cells_gain_and_janus_prices_it(df):
    item = df.ParsedItem(raw="o314", name="o314", target_date="11/4")
    w = df.RouteResult(item=item, step="0n", col_num=43,
                       write_value=70, write_mode="max")
    assert df._credited_0n(w, {"43": "40"}, {"43": "70"}) == 30   # 2nd session
    assert df._credited_0n(w, {"43": "70"}, {"43": "70"}) == 0    # Toggl miss: no-op
    assert df._credited_0n(w, {"43": ""}, {"43": "40"}) == 40
    w.write_mode = None
    assert df._credited_0n(w, {"43": "40"}, {"43": "70"}) is None
    # janus: a 2nd 30m session is 30分, not the day's 70
    assert jd.did_points({"step": "0n", "name": "o314", "value": 70, "credited": 30}) == 30
    assert jd.did_points({"step": "0n", "name": "o314", "value": 70}) == 70
