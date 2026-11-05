"""Regression (2026-10-26): dream-intake took 2h01m every night (duration_ms
~7.26M) while every source reported <=30s. One source thread hung (Agency
stuck in interactive Entra auth); `with ThreadPoolExecutor` joined it on exit.
run_sources must return within its timeout even if a source never returns."""
import importlib.util
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("dream_intake", HERE / "dream-intake.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_hung_source_does_not_block():
    di = _load()
    release = threading.Event()
    di._SOURCES.clear()
    di._SOURCES["fast"] = {"fn": lambda: {"ok": 1}, "desc": "fast"}
    di._SOURCES["hung"] = {"fn": lambda: release.wait(30), "desc": "hung"}
    t0 = time.time()
    try:
        res = di.run_sources(timeout=1)
    finally:
        release.set()
    assert time.time() - t0 < 3
    assert res["fast"]["status"] == "ok"
    assert res["hung"]["status"] == "timeout"


def test_timeout_is_shared_deadline_not_per_source():
    di = _load()
    release = threading.Event()
    di._SOURCES.clear()
    for i in range(4):
        di._SOURCES[f"hung{i}"] = {"fn": lambda: release.wait(30), "desc": "h"}
    t0 = time.time()
    try:
        res = di.run_sources(timeout=1)
    finally:
        release.set()
    assert time.time() - t0 < 3, "4 hung sources must not cost 4x the timeout"
    assert all(r["status"] == "timeout" for r in res.values())
