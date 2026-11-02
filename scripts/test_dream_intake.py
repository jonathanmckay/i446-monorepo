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


# ── gmail_inbox: every account, Promotions excluded, uncapped count ────────

class _Req:
    def __init__(self, v):
        self.v = v

    def execute(self):
        return self.v


class _FakeGmail:
    """Minimal users().messages().list/get chain."""

    def __init__(self, n, page=100):
        self.n, self.page, self.queries = n, page, []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults, pageToken=None):
        self.queries.append(q)
        start = int(pageToken or 0)
        ids = [{"id": f"m{i}"} for i in range(start, min(self.n, start + self.page))]
        nxt = start + self.page
        return _Req({"messages": ids, **({"nextPageToken": str(nxt)} if nxt < self.n else {})})

    def get(self, userId, id, format, metadataHeaders):
        return _Req({"id": id, "internalDate": str(int(time.time() * 1000)),
                     "payload": {"headers": [{"name": "Subject", "value": id}]}})


def test_gmail_inbox_counts_every_account(monkeypatch):
    """Regression (v176): intake said 'Gmail unread 2' (the personal
    jonathan.b.mckay@gmail.com) while mckay@m5c7.com had 19 non-promo
    unread. Both accounts must be counted, labelled, and summed."""
    fakes = {"m5c7": _FakeGmail(19), "jbm": _FakeGmail(2)}
    monkeypatch.setitem(di._CREDS, "gmail_accounts", [
        {"name": "m5c7", "tokens": "m5c7", "secrets": "x"},
        {"name": "jbm", "tokens": "jbm", "secrets": "x"}])
    monkeypatch.setattr(di, "_build_google_creds", lambda t, s, **k: t)
    monkeypatch.setattr(di, "_google_service", lambda svc, v, creds: fakes[creds])
    d = di._gmail_inbox()
    assert d["unread_count"] == 21
    assert d["by_account"]["m5c7"]["unread_count"] == 19
    assert {m["account"] for m in d["messages"]} == {"m5c7", "jbm"}
    assert all("-category:promotions" in q for f in fakes.values() for q in f.queries)


def test_gmail_count_not_capped_by_metadata_fetch():
    count, msgs = di._gmail_account_unread(_FakeGmail(230))
    assert count == 230 and len(msgs) == di.GMAIL_METADATA_CAP


def test_gmail_one_account_failing_keeps_the_other(monkeypatch):
    monkeypatch.setitem(di._CREDS, "gmail_accounts", [
        {"name": "m5c7", "tokens": "m5c7", "secrets": "x"},
        {"name": "jbm", "tokens": "jbm", "secrets": "x"}])

    def creds(t, s, **k):
        if t == "jbm":
            raise ValueError("revoked")
        return t
    monkeypatch.setattr(di, "_build_google_creds", creds)
    monkeypatch.setattr(di, "_google_service", lambda *a: _FakeGmail(3))
    d = di._gmail_inbox()
    assert d["unread_count"] == 3 and "jbm" in d["errors"]


def test_load_credentials_lists_m5c7_first():
    di.load_credentials()
    accts = di._CREDS["gmail_accounts"]
    assert [a["name"] for a in accts] == ["m5c7", "jbm"]
    assert accts[0]["tokens"].name == "tokens.json"
