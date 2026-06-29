"""Toggl rate-limit resilience: bursty non-forced fetches coalesce, a 402 puts
the app into a cooldown that skips further Toggl reads (so it stops hammering),
and forced/manual paths behave correctly."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("tg_tui_rl", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_rl"] = mod
    spec.loader.exec_module(mod)
    mod.STATE.day_offset = 0
    mod.STATE.toggl_blocked_until = 0.0
    mod.STATE.last_toggl_fetch = 0.0
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
