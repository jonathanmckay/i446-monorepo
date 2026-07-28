"""Toggl rate-limit resilience: bursty non-forced fetches coalesce, a 402 puts
the app into a cooldown that skips further Toggl reads (so it stops hammering),
and forced/manual paths behave correctly."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_rl", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_rl"] = mod
    spec.loader.exec_module(mod)
    mod.STATE.day_offset = 0
    mod.STATE.toggl_blocked_until = 0.0
    mod.STATE.last_toggl_fetch = 0.0
    # These tests exercise the IN-PROCESS cooldown; isolate them from the shared
    # cross-process cooldown file (covered by test_throttle.py), whose real state
    # would otherwise leak a live cooldown into _toggl_blocked().
    mod.toggl_throttle.cooling_down = lambda: False
    return mod


def test_fetch_today_coalesces_bursts(monkeypatch):
    m = _load()
    calls = []
    monkeypatch.setattr(m.toggl_api, "get_entries", lambda **k: calls.append(1) or [])
    m.fetch_today()           # last_toggl_fetch=0 → runs
    m.fetch_today()           # within TOGGL_MIN_INTERVAL → coalesced (skipped)
    assert len(calls) == 1, "rapid non-forced fetches must coalesce"
    m.fetch_today(force=True)  # deliberate action → bypasses the throttle
    assert len(calls) == 2


def test_402_starts_cooldown_then_skips_toggl(monkeypatch):
    m = _load()
    monkeypatch.setattr(m.toggl_api, "get_entries",
                        lambda **k: (_ for _ in ()).throw(Exception("HTTP 402 rate limit")))
    m.fetch_today(force=True)  # hits 402
    assert m.STATE.toggl_blocked_until > m.time.monotonic(), "402 must start a cooldown"

    # During the cooldown, even a forced fetch is skipped — no API call made.
    calls = []
    monkeypatch.setattr(m.toggl_api, "get_entries", lambda **k: calls.append(1) or [])
    m.fetch_today(force=True)
    assert calls == [], "must not call Toggl during the post-402 cooldown"


def test_fetch_current_skips_during_cooldown(monkeypatch):
    m = _load()
    m.STATE.toggl_blocked_until = m.time.monotonic() + 60
    calls = []
    monkeypatch.setattr(m.toggl_api, "get_current", lambda: calls.append(1))
    monkeypatch.setattr(m.toggl_api, "get_current_cached", lambda: calls.append(1))
    m.fetch_current()
    m.fetch_current(cached=True)
    assert calls == [], "fetch_current must be a no-op during the cooldown"


def test_cooldown_expiry_allows_fetch_again(monkeypatch):
    m = _load()
    m.STATE.toggl_blocked_until = m.time.monotonic() - 1  # already expired
    calls = []
    monkeypatch.setattr(m.toggl_api, "get_entries", lambda **k: calls.append(1) or [])
    m.fetch_today(force=True)
    assert calls == [1], "an expired cooldown must let fetches resume"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


def test_shared_cooldown_silences_pollers(monkeypatch):
    """A 402 tripped by any process (shared cooldown) must silence janus's
    pollers even when this process has no in-process back-off."""
    m = _load()
    m.STATE.toggl_blocked_until = 0.0          # no in-process cooldown
    m.toggl_throttle.cooling_down = lambda: True  # but the SHARED file says 402
    calls = []
    monkeypatch.setattr(m.toggl_api, "get_entries", lambda **k: calls.append(1) or [])
    m.fetch_today(force=True)
    assert calls == [], "shared cooldown must skip the fetch"
    assert m._toggl_blocked() is True


def test_sigusr1_refresh_debounces_burst(monkeypatch):
    """A burst of mutation nudges collapses to a single refresh (one pair of
    Toggl GETs), instead of one refresh per /tg."""
    import asyncio
    m = _load()
    m.toggl_throttle.cooling_down = lambda: False

    class _App:
        def invalidate(self):
            pass

    m.app = _App()
    fetched = {"n": 0}
    monkeypatch.setattr(m, "fetch_current", lambda *a, **k: fetched.__setitem__("n", fetched["n"] + 1))
    monkeypatch.setattr(m, "fetch_today", lambda *a, **k: None)
    monkeypatch.setattr(m, "fetch_short_names", lambda *a, **k: None)
    monkeypatch.setattr(m, "_bg_fetch", lambda *a, **k: None)
    monkeypatch.setattr(m, "flash", lambda *a, **k: None)
    # Make the debounce sleep yield once (fast) instead of a real 0.4s. Capture
    # the real sleep first — m.asyncio is the same module object, so patching it
    # would otherwise make the lambda recurse into itself.
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(m.asyncio, "sleep", lambda s: _real_sleep(0))
    m.STATE.sigusr1_token = 0

    async def _burst():
        await asyncio.gather(m._sigusr1_refresh(), m._sigusr1_refresh(),
                             m._sigusr1_refresh())

    asyncio.run(_burst())
    assert fetched["n"] == 1, "three rapid nudges must yield exactly one refresh"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
