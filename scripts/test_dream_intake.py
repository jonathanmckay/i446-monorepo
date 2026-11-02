"""Tests for scripts/dream-intake.py sources that broke Dream's view of JM's
inboxes and calendars (Dream v176 self-improvement, 2026-11-02)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import inspect
import json
import os
import time
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "dream_intake", Path(__file__).parent / "dream-intake.py")
di = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(di)


# ── outlook_inbox / outlook_calendar: Straylight cache, never live ─────────

@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(di, "JANUS_CACHE", tmp_path)
    monkeypatch.setattr(di, "_local_today", lambda: dt.date(2026, 11, 2))
    return tmp_path


def _snap(path: Path, msgs, age_h=0.1):
    path.write_text(json.dumps({"generated_at": "2026-11-02T03:00:00-08:00",
                                "window_hours": 48, "count": len(msgs),
                                "messages": msgs}))
    t = time.time() - age_h * 3600
    os.utime(path, (t, t))


MSG = {"sender": "Ben Lee", "sender_email": "ben@microsoft.com",
       "subject": "XCORE readout", "received": "2026-11-02T01:00:00Z",
       "preview": "x" * 300, "importance": "high", "isRead": False,
       "conversationId": "c1"}


def test_outlook_sources_never_call_agency():
    """Regression: the live Agency call timed out at 30s every night on ix
    (no work auth from cron). The sources must only read the cache."""
    for fn in (di._outlook_inbox, di._outlook_calendar):
        assert "agency_mcp" not in inspect.getsource(fn)
        assert "call_tool" not in inspect.getsource(fn)


def test_outlook_inbox_reads_today_snapshot(cache):
    _snap(cache / "outlook-inbox-2026-11-02.json",
          [MSG, {**MSG, "subject": "read one", "isRead": True}])
    out = di.run_sources(["outlook_inbox"])["outlook_inbox"]
    assert out["status"] == "ok"
    d = out["data"]
    assert d["unread_count"] == 1
    assert d["unread"][0]["from"] == "Ben Lee"
    assert d["high_importance"] == 1
    assert "_status" not in d


def test_outlook_inbox_falls_back_to_yesterday_and_flags_stale(cache):
    _snap(cache / "outlook-inbox-2026-11-01.json", [MSG], age_h=8)
    out = di.run_sources(["outlook_inbox"])["outlook_inbox"]
    assert out["status"] == "stale"
    assert out["data"]["unread_count"] == 1


def test_outlook_inbox_missing_is_instant(cache):
    t0 = time.time()
    out = di.run_sources(["outlook_inbox"])["outlook_inbox"]
    assert time.time() - t0 < 1
    assert out["status"] == "missing"
    assert "outlook-cache-daemon" in out["error"]


def test_outlook_calendar_reads_cache(cache):
    (cache / "outlook-2026-11-02.json").write_text(json.dumps([{
        "subject": "BL:JM 1:1", "start": "2026-11-02T21:30:00.0000000",
        "end": "2026-11-02T22:00:00.0000000", "start_tz": "UTC",
        "is_all_day": False}]))
    out = di.run_sources(["outlook_calendar"])["outlook_calendar"]
    assert out["status"] == "ok"
    assert out["data"]["events"][0]["subject"] == "BL:JM 1:1"
    assert out["data"]["events"][0]["day"] == "2026-11-02"


def test_outlook_calendar_without_today_file_is_stale(cache):
    (cache / "outlook-2026-11-01.json").write_text("[]")
    assert di.run_sources(["outlook_calendar"])["outlook_calendar"]["status"] == "stale"
