#!/usr/bin/env python3
"""Regression (2026-08-11): "dtd web hangs on loading (load time > 20s)".

_refresh_cache_if_stale() used to call subprocess.run(...) SYNCHRONOUSLY,
inline in the request handler, whenever the cache passed CACHE_MAX_AGE
(180s) — which is most normal page loads. Reproduced live: consecutive
/api/tasks calls measured 18.2s, 15.7s, then 66.8s (WORSE each time, since
nothing stopped overlapping requests from each spawning their own refresh
subprocess and piling concurrent Todoist calls into rate limiting), even
though the underlying script alone takes ~2s run directly.

Fix: the staleness-triggered case (force=False) now kicks the refresh off
in a background thread — deduped by a module lock so at most one is ever in
flight — and returns immediately, serving whatever's already cached. The
explicit force=True case (↻ button / post-quick-add reload) still blocks
synchronously, since the caller is deliberately waiting on fresh data.
"""
import datetime as dt
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtd  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_lock():
    if dtd._refresh_lock.locked():
        dtd._refresh_lock.release()
    yield
    if dtd._refresh_lock.locked():
        dtd._refresh_lock.release()


def _write_cache(tmp_path, monkeypatch, age_seconds):
    cache_f = tmp_path / "task-queue.json"
    updated = (dt.datetime.now() - dt.timedelta(seconds=age_seconds)).isoformat()
    cache_f.write_text(json.dumps({"updated": updated}))
    monkeypatch.setattr(dtd, "CACHE", cache_f)
    return cache_f


def test_stale_refresh_does_not_block_the_caller(monkeypatch, tmp_path):
    _write_cache(tmp_path, monkeypatch, dtd.CACHE_MAX_AGE + 10)
    on_main_thread = []
    release = threading.Event()

    def fake_run(*a, **k):
        on_main_thread.append(threading.current_thread() is threading.main_thread())
        release.wait(timeout=2)  # simulate a slow Todoist round-trip

    monkeypatch.setattr(dtd.subprocess, "run", fake_run)

    t0 = time.monotonic()
    dtd._refresh_cache_if_stale(force=False)
    elapsed = time.monotonic() - t0
    release.set()
    time.sleep(0.05)

    assert elapsed < 0.5, \
        "a stale-triggered refresh must return immediately, not block on the subprocess"
    assert on_main_thread and on_main_thread[0] is False, \
        "the refresh subprocess must run off the request-handling thread"


def test_force_refresh_still_blocks_synchronously(monkeypatch, tmp_path):
    _write_cache(tmp_path, monkeypatch, 0)  # fresh cache — force must refresh anyway
    on_main_thread = []
    monkeypatch.setattr(dtd.subprocess, "run",
                        lambda *a, **k: on_main_thread.append(
                            threading.current_thread() is threading.main_thread()))

    dtd._refresh_cache_if_stale(force=True)
    assert on_main_thread == [True], \
        "force=True (explicit reload) must still refresh synchronously on the caller's thread"


def test_overlapping_stale_refreshes_are_deduped(monkeypatch, tmp_path):
    _write_cache(tmp_path, monkeypatch, dtd.CACHE_MAX_AGE + 10)
    started = threading.Event()
    finish = threading.Event()
    call_count = [0]

    def fake_run(*a, **k):
        call_count[0] += 1
        started.set()
        finish.wait(timeout=2)

    monkeypatch.setattr(dtd.subprocess, "run", fake_run)

    dtd._refresh_cache_if_stale(force=False)  # spawns the one in-flight refresh
    assert started.wait(timeout=1), "the first refresh should have started"
    dtd._refresh_cache_if_stale(force=False)  # must be a no-op: lock already held
    finish.set()
    time.sleep(0.1)
    assert call_count[0] == 1, \
        "a refresh already in flight must not be duplicated by an overlapping request"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
