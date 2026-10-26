"""Regression (2026-10-25): "precache jm dash so it loads basically instantly."

Every payload the page fetches on load used to be built on demand once its
5-minute TTL ran out (main data ~1.3s, per-block chart ~1.4-2.7s, today's
points ~1s live from Excel with no cache at all), so a load after a break
waited on them. A warmer thread now rebuilds them before they expire."""
import importlib.util
import sys
from pathlib import Path


def _load():
    spec = importlib.util.spec_from_file_location("dashboard_warm", Path(__file__).parent / "dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dashboard_warm"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_warm_once_fills_every_page_load_cache(monkeypatch):
    d = _load()
    monkeypatch.setattr(d, "_build_points_today", lambda: {"value": 1234.0})
    monkeypatch.setattr(d, "_build_api_data", lambda: {"k": "data"})
    monkeypatch.setattr(d, "_build_weekly_data", lambda offset=0: {"k": "weekly"})
    monkeypatch.setattr(d, "_build_granular_chart_data", lambda g, offset=0: {"k": g})
    d._warm_once()
    assert d._POINTS_TODAY_CACHE["payload"] == {"value": 1234.0}
    assert d._api_data_cached() == {"k": "data"}          # served from cache, no rebuild
    assert d._weekly_cached() == {"k": "weekly"}
    assert d._granular_cached("block", 0) == {"k": "block"}
    with d.app.test_request_context("/api/points-today"):
        assert d.api_points_today().get_json() == {"value": 1234.0}   # cached, no live Excel read


def test_points_only_tick_leaves_data_caches_alone(monkeypatch):
    d = _load()
    monkeypatch.setattr(d, "_build_points_today", lambda: {"value": 1.0})
    monkeypatch.setattr(d, "_build_api_data", lambda: (_ for _ in ()).throw(AssertionError("not on a points tick")))
    d._warm_once(data=False)
    assert d._POINTS_TODAY_CACHE["payload"] == {"value": 1.0}


def test_a_failing_builder_does_not_stop_the_others(monkeypatch):
    d = _load()
    monkeypatch.setattr(d, "_build_points_today", lambda: (_ for _ in ()).throw(RuntimeError("excel busy")))
    monkeypatch.setattr(d, "_build_api_data", lambda: {"k": "data"})
    monkeypatch.setattr(d, "_build_weekly_data", lambda offset=0: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(d, "_build_granular_chart_data", lambda g, offset=0: {"k": g})
    d._warm_once()
    assert d._API_DATA_CACHE["payload"] == {"k": "data"}
    assert d._GRANULAR_CACHE[("block", 0)]["payload"] == {"k": "block"}


def test_server_starts_the_warmer():
    src = (Path(__file__).parent / "dashboard.py").read_text()
    main = src[src.index('if __name__ == "__main__":'):]
    assert "start_warmer()" in main.split("app.run")[0]
