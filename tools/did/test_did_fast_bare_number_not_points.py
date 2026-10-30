"""Regression: a bare number in task text must never become points.

2026-10-22 19:56: janus-mobile /done sent did-fast
"set up quarterly readjustment 2028 1922-1956 @i9" (the timer was named after
a Todoist task whose text ended in the year "2028"). parse_input took the
trailing 2028 as typed minutes, and the variable path (Step 0.4) credited
minutes-as-points: +2028 to 0分!R. Two fixes:
  1. with an HHMM-HHMM range present, a trailing bare number stays in the name;
  2. an inferred (non-[N]) variable credit above MAX_INFERRED_POINTS is refused.

Run: python3 -m pytest tools/did/test_did_fast_bare_number_not_points.py -v
"""
import importlib.util
import sys
from pathlib import Path

DID_FAST = Path(__file__).parent / "did-fast.py"


def _load():
    spec = importlib.util.spec_from_file_location("did_fast_bare_number", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _route(mod, monkeypatch, raw):
    monkeypatch.setattr(mod, "_live_todoist_search", lambda q: None)
    items = mod.parse_input(raw)
    return items, mod.route_items(items, headers={"0n": {}, "1n": {}}, tq={},
                                  skip_todoist=True)


def test_trailing_year_with_range_is_name_not_minutes(monkeypatch):
    mod = _load()
    items, results = _route(mod, monkeypatch,
                            "set up quarterly readjustment 2028 1922-1956 @i9")
    assert items[0].time_value is None
    assert items[0].time_range == ("1922", "1956")
    assert items[0].name == "set up quarterly readjustment 2028"
    r = results[0]
    assert r.step == "variable"
    assert r.fen_points == 34, "the range's minutes, not the year"


def test_typed_minutes_without_range_still_work(monkeypatch):
    mod = _load()
    items, results = _route(mod, monkeypatch, "read COD deck 30 @i9")
    assert items[0].time_value == 30
    assert results[0].fen_points == 30


def test_inferred_credit_over_cap_is_refused(monkeypatch):
    mod = _load()
    _, results = _route(mod, monkeypatch, "set up quarterly readjustment 2028 @i9")
    r = results[0]
    assert r.step == "refused"
    assert not r.fen_points
    assert str(mod.MAX_INFERRED_POINTS) in r.error


def test_forgotten_timer_range_over_cap_is_refused(monkeypatch):
    mod = _load()
    _, results = _route(mod, monkeypatch, "deep work 0800-1800 @i9")  # 600 min
    assert results[0].step == "refused"


def test_explicit_points_over_cap_still_credit(monkeypatch):
    mod = _load()
    _, results = _route(mod, monkeypatch, "close the l92 purchase 2028 [1590] @m5x2")
    assert results[0].step == "variable"
    assert results[0].fen_points == 1590


def test_cap_shared_with_janus_done():
    mod = _load()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
    import janus_done
    assert mod.MAX_INFERRED_POINTS == janus_done.MAX_INFERRED_POINTS == 500
