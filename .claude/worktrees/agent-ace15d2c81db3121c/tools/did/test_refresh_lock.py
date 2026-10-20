"""Regression (2026-07-01): '/0g' set goals but they didn't show in dtd at once.

The --refresh-cache the skill runs foreground took a NON-BLOCKING lock and, if a
concurrent refresh (periodic daemon / dtd auto-refresh / ticker) held it, silently
returned the stale cache — so a just-created goal only reached dtd on the daemon's
next ~3-min cycle. An EXPLICIT refresh must wait for the lock, not skip.
"""
import importlib.util
import inspect
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("did_fast_lock", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_spec)
sys.modules["did_fast_lock"] = df
_spec.loader.exec_module(df)


def test_refresh_lock_flag_conditional_on_block():
    """The lock is non-blocking only when block=False."""
    src = inspect.getsource(df.refresh_task_queue)
    assert "def refresh_task_queue(block" in src, "must take a block param"
    assert "LOCK_EX if block else" in src, "block=True → plain LOCK_EX (wait), else LOCK_NB"


def test_cli_refresh_cache_uses_block():
    """The --refresh-cache CLI entry must request a blocking refresh."""
    src = (_HERE / "did-fast.py").read_text()
    i = src.index('"--refresh-cache"')
    window = src[i:i + 400]
    assert "refresh_task_queue(block=True)" in window, \
        "explicit --refresh-cache must block on the lock so it can't silently skip"


def test_nonblocking_skips_but_blocking_waits(tmp_path, monkeypatch):
    """Behavioural: with the lock held, block=False returns the stale cache
    without running; block=True waits for release, then runs."""
    import fcntl
    import threading
    import time

    monkeypatch.setattr(df, "TASK_QUEUE_PATH", tmp_path / "tq.json")
    ran = {"count": 0}

    def _fake_inner():
        ran["count"] += 1
        return {"ran": True}

    monkeypatch.setattr(df, "_refresh_task_queue_inner", _fake_inner)

    held = open(tmp_path / "tq.lock", "w")
    fcntl.flock(held, fcntl.LOCK_EX)

    # Non-blocking: lock held → skip, return existing ({} — no cache yet), inner NOT run.
    assert df.refresh_task_queue(block=False) == {}
    assert ran["count"] == 0, "non-blocking must not run the refresh while locked"

    # Release the lock shortly, from another thread.
    def _release():
        time.sleep(0.3)
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()

    threading.Thread(target=_release).start()
    t0 = time.monotonic()
    result = df.refresh_task_queue(block=True)  # must WAIT then run
    elapsed = time.monotonic() - t0

    assert result == {"ran": True}, "blocking refresh must actually run once free"
    assert ran["count"] == 1
    assert elapsed >= 0.25, "blocking refresh must wait for the lock, not skip"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
