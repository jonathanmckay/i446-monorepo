"""User report 2026-09-05: "now I don't see any time entries from this
morning."

fetch_today() already tracks STATE.entries_known -- False until the first
CONFIRMED Toggl read, reset on a failed one (e.g. a 402 rate limit) -- because
an empty STATE.entries is indistinguishable from a genuinely tracked-nothing
day. _block_gaps() already guards its own "empty -> HH:MM" gap-flash against
this (2026-07-15 comment in janus.py), but render_morning()'s actual entry
listing never got the same guard: it just iterates STATE.entries directly. A
janus process that starts (or restarts) while Toggl's account-level hourly
quota is exhausted never gets a confirmed fetch, STATE.entries stays at its
initial empty list, and the whole morning renders as confidently blank --
indistinguishable from real data loss.

Fix: render_morning() now prepends a dim warning line when
STATE.entries_known is False, instead of silently rendering every block as
empty.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_morning_known", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_morning_known"] = mod
    spec.loader.exec_module(mod)
    return mod


def _setup_common(mod):
    mod.STATE.current_known = True
    mod.STATE.entries_known = True
    mod.STATE.entries = []
    mod.STATE.entries_yday = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.day_offset = 0
    mod.STATE.current = None


def test_unconfirmed_entries_render_a_warning_not_a_silent_blank():
    mod = _load_tui()
    _setup_common(mod)
    mod.STATE.entries_known = False  # cold-start 402: never confirmed a fetch
    text = "".join(t for _, t, *_ in mod.render_morning())
    assert "unconfirmed" in text.lower(), (
        "an unconfirmed (rate-limited/never-fetched) morning must say so, "
        "not silently render as if genuinely empty -- the concrete symptom "
        "is a user reading a blank morning as lost data")


def test_confirmed_empty_morning_has_no_warning():
    """Regression guard: a CONFIRMED read that's genuinely empty (a real
    no-activity morning) must not show the unconfirmed warning."""
    mod = _load_tui()
    _setup_common(mod)
    mod.STATE.entries_known = True
    text = "".join(t for _, t, *_ in mod.render_morning())
    assert "unconfirmed" not in text.lower()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
