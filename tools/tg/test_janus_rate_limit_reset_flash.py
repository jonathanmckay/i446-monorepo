"""Regression (2026-10-16): "janus isn't updating from toggl, it hit the rate
limit and now is just stalling". janus flashed "backing off 60s", then went
silent while the shared hourly-cap cooldown (armed by any process) kept every
fetch skipped. Now _note_rate_limit holds off for the shared cooldown's
remaining time and keeps the reset clock on screen for the whole wait, and a
fetch skipped because of ANOTHER process's cooldown says so too.
"""
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_rl_flash", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_rl_flash"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_note_rate_limit_uses_shared_reset_estimate():
    mod = _load()
    mod.toggl_throttle.cooldown_remaining = lambda *a, **k: 653.0
    t0 = time.monotonic()
    mod._note_rate_limit()
    assert max(mod.STATE.toggl_blocked_until.values()) - t0 >= 652, "must hold off until the server's reset"
    assert mod.STATE.flash_until - t0 >= 652, "reason must stay on screen for the whole wait"
    assert "resets" in mod.STATE.flash and "10m" in mod.STATE.flash, mod.STATE.flash


def test_note_rate_limit_never_below_local_default():
    mod = _load()
    mod.toggl_throttle.cooldown_remaining = lambda *a, **k: 0.0
    t0 = time.monotonic()
    mod._note_rate_limit()
    assert mod.RATE_LIMIT_COOLDOWN - 1 <= max(mod.STATE.toggl_blocked_until.values()) - t0 <= mod.RATE_LIMIT_COOLDOWN + 1


def test_fetch_skipped_by_another_processs_cooldown_is_explained():
    mod = _load()
    mod.STATE.toggl_blocked_until = {"user": 0.0, "ws": 0.0}
    mod.STATE.flash, mod.STATE.flash_until = "", 0.0
    mod.toggl_throttle.cooling_down = lambda *a, **k: True
    mod.toggl_throttle.cooldown_remaining = lambda *a, **k: 400.0
    calls = []
    mod.toggl_api.get_current_cached = lambda *a, **k: calls.append(1)
    mod.fetch_current(cached=True)
    assert not calls, "a blocked fetch must not touch Toggl"
    assert "resets" in mod.STATE.flash and mod.STATE.flash_until > time.monotonic() + 390
