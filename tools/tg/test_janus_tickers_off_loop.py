"""Regression (2026-11-10): janus froze for 2h on Straylight. ticker_current and
ticker_today called fetch_current()/fetch_today() synchronously inside the
asyncio loop, so a stalled Toggl request blocked rendering and input. They must
go through _bg_fetch (daemon thread) like ticker_gcal, and fetches must be
serialized by _FETCH_LOCK so a ticker skips when one is in flight and a forced
fetch never has its fresh state overwritten by a late ticker."""
import ast
import importlib.util
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _ticker_bodies():
    tree = ast.parse((HERE / "janus.py").read_text())
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name in ("ticker_current", "ticker_today")}


def _called_names(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", None))
    return out


def test_tickers_do_not_call_toggl_fetches_on_the_event_loop():
    bodies = _ticker_bodies()
    assert set(bodies) == {"ticker_current", "ticker_today"}
    for name, fn in bodies.items():
        # Direct statement-level calls (not inside a lambda handed to _bg_fetch).
        direct = set()
        for stmt in ast.walk(fn):
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                f = stmt.value.func
                direct.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", None))
        assert not ({"fetch_current", "fetch_today"} & direct), f"{name} fetches on the loop"
        assert "_bg_fetch" in _called_names(fn), f"{name} must use _bg_fetch"


def _load():
    spec = importlib.util.spec_from_file_location("janus_tickers", HERE / "janus.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def test_ticker_fetch_skips_when_a_fetch_is_in_flight_and_is_reentrant():
    j = _load()
    calls = []
    # Lock held by another thread (a forced fetch mid-stall): the tick is skipped.
    held = threading.Event(); release = threading.Event()

    def holder():
        with j._FETCH_LOCK:
            held.set(); release.wait(5)
    t = threading.Thread(target=holder, daemon=True); t.start(); held.wait(5)
    assert j._ticker_fetch(lambda: calls.append("x")) is False
    assert calls == []
    release.set(); t.join(5)
    # Free lock: runs, and a fetch that itself takes _FETCH_LOCK does not deadlock.
    def locked_fn():
        with j._FETCH_LOCK:
            calls.append("ran")
    assert j._ticker_fetch(locked_fn) is True
    assert calls == ["ran"]
