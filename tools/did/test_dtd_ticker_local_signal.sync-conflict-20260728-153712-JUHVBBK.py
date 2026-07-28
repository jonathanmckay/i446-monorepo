#!/usr/bin/env python3
"""Regression: dtd's footer timer must update snappily on a dtd-initiated
start/complete instead of lagging up to POLL (~12s) on the Toggl poll.

Bug (2026-06-24): starting/switching a timer inside dtd took ~10s to show in
the footer. The ticker only learned about timer changes via api.get_current_cached(),
gated by POLL=12s. dtd's start/complete bindings already write the running entry
to $DTD_TIMER (`desc<TAB>start_epoch`, emptied on stop) the instant they fire, so
the ticker should watch that local file every tick (no network) and apply it at once.

These tests verify the wiring (dtd.sh passes the timer file; the ticker accepts it),
the parse helper, and that the poll's idle-clear is guarded so a fresh local start
isn't clobbered by a stale shared cache.
"""
import importlib.util
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = HERE / "dtd.sh"
TICKER = HERE / "dtd-ticker.py"


def _load_ticker():
    spec = importlib.util.spec_from_file_location("dtd_ticker", TICKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dtd_passes_timer_file_to_ticker():
    """dtd.sh must hand $DTD_TIMER to the ticker so it can watch the fast signal."""
    src = DTD.read_text()
    assert '"$DTD_TICKER" "$DTD_PORT" "$DTD_TIMER"' in src, (
        "ticker launch must pass $DTD_TIMER as the local-signal source")


def test_read_timer_file_parses_running_entry(tmp_path):
    mod = _load_ticker()
    f = tmp_path / "dtd.timer"
    started = time.time() - 90
    f.write_text(f"work\t{started}\n")
    start, desc, mtime = mod._read_timer_file(f)
    assert abs(start - started) < 1
    assert desc == "work"
    assert mtime is not None


def test_read_timer_file_empty_means_idle(tmp_path):
    mod = _load_ticker()
    f = tmp_path / "dtd.timer"
    f.write_text("")  # dtd clears the file on complete/stop
    start, desc, mtime = mod._read_timer_file(f)
    assert start is None and desc == ""
    assert mtime is not None  # file exists, so a change is still detectable


def test_read_timer_file_missing_is_safe(tmp_path):
    mod = _load_ticker()
    start, desc, mtime = mod._read_timer_file(tmp_path / "nope.timer")
    assert (start, desc, mtime) == (None, "", None)
    # None path (back-compat: no timer file arg) must not raise either.
    assert mod._read_timer_file(None) == (None, "", None)


def test_read_timer_file_strips_parens(tmp_path):
    """Parens would prematurely terminate fzf's change-footer(...) action."""
    mod = _load_ticker()
    f = tmp_path / "dtd.timer"
    f.write_text(f"call mom (家)\t{time.time()}\n")
    _, desc, _ = mod._read_timer_file(f)
    assert "(" not in desc and ")" not in desc


def test_poll_idle_clear_is_guarded_against_fresh_local_start():
    """The poll must not clear a just-started local timer when Toggl reads idle
    (the shared cache can lag by its TTL). Only timers older than POLL clear."""
    src = TICKER.read_text()
    assert "now - start > POLL" in src, (
        "idle-clear must be guarded so a fresh local start survives a stale cache")
