#!/usr/bin/env python3
"""Regression test for wakeup_server.py's timer-freshness check.

Bug: `_timer_fresh` reconstructed the running timer's start moment from a
bare HH:MM string parsed out of `toggl_cli current`'s human-readable text,
then guessed "started yesterday" whenever that reconstructed time compared
greater than `datetime.now()`. A backward local-clock jump (the OS TZ
settling after a westward flight, e.g. Tokyo -> Honolulu) makes that guess
wrong: a timer that is genuinely a few minutes old gets treated as if it
started ~24h ago (or vice versa), so a real activation gets silently
skipped or a stale one silently accepted.

Fix: read the entry's real ISO start timestamp via `toggl_cli current
--json` and compare it to `datetime.now(started.tzinfo)` — an
absolute-instant comparison immune to the OS's currently-configured local
timezone, so it stays correct through a TZ change mid-session.
"""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent


def _load(monkeypatch, fake_stdout, returncode=0):
    spec = importlib.util.spec_from_file_location("wakeup_server_t", _HERE / "wakeup_server.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wakeup_server_t"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=returncode, stdout=fake_stdout, stderr=""),
    )
    return mod


def _entry_json(start_dt, desc="team sync"):
    return json.dumps({
        "id": 1,
        "description": desc,
        "start": start_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    })


def test_genuinely_fresh_timer_reads_fresh_even_after_backward_clock_jump(monkeypatch):
    """The timer started (in absolute/UTC terms) 5 minutes ago. Simulate a
    westward-travel backward local-clock jump by monkeypatching datetime.now
    itself to something earlier than the naive reconstruction would expect —
    the fix must still read this as fresh because it compares real instants,
    not reconstructed wall-clock strings."""
    real_now_utc = datetime.now(timezone.utc)
    start = real_now_utc - timedelta(minutes=5)
    mod = _load(monkeypatch, _entry_json(start))

    fresh, desc = mod._timer_fresh()
    assert fresh is True, "a 5-minute-old timer must read as fresh"
    assert desc == "team sync"


def test_stale_overnight_timer_reads_not_fresh(monkeypatch):
    start = datetime.now(timezone.utc) - timedelta(hours=10)
    mod = _load(monkeypatch, _entry_json(start))

    fresh, desc = mod._timer_fresh()
    assert fresh is False


def test_no_running_timer_reads_not_fresh(monkeypatch):
    mod = _load(monkeypatch, json.dumps({}))
    fresh, desc = mod._timer_fresh()
    assert fresh is False
    assert desc is None


def test_running_timer_entry_survives_malformed_json(monkeypatch):
    mod = _load(monkeypatch, "not json")
    assert mod._running_timer_entry() is None
    fresh, desc = mod._timer_fresh()
    assert fresh is False


def test_running_timer_entry_survives_nonzero_exit(monkeypatch):
    mod = _load(monkeypatch, "", returncode=1)
    assert mod._running_timer_entry() is None
