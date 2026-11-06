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


def _no_gcal(today):
    raise RuntimeError("offline")


def test_outlook_calendar_without_today_file_is_stale(cache, monkeypatch):
    monkeypatch.setattr(di, "_gcal_import_events", _no_gcal)
    (cache / "outlook-2026-11-01.json").write_text("[]")
    out = di.run_sources(["outlook_calendar"])["outlook_calendar"]
    assert out["status"] == "stale"
    assert out["data"]["gcal_import_error"] == "RuntimeError: offline"


# v180: stale/missing cache -> MSFT (Slow Sync) Google import calendar.
IMPORT_EV = {"day": "2026-11-02", "subject": "JA: JM 1:1",
             "start": "2026-11-02T09:30:00-08:00", "end": "2026-11-02T10:00:00-08:00",
             "tz": "America/Los_Angeles", "all_day": False}


def test_outlook_calendar_stale_cache_uses_import_calendar(cache, monkeypatch):
    monkeypatch.setattr(di, "_gcal_import_events", lambda today: [IMPORT_EV])
    (cache / "outlook-2026-11-01.json").write_text("[]")
    out = di.run_sources(["outlook_calendar"])["outlook_calendar"]
    assert out["status"] == "ok"
    d = out["data"]
    assert d["via"] == "gcal_import" and d["cache_status"] == "stale"
    assert d["calendar_id"] == "l20n3a79v2lq68fod4de3lvp1ba2iqft@import.calendar.google.com"
    assert d["events"] == [IMPORT_EV]


def test_outlook_calendar_missing_cache_uses_import_calendar(cache, monkeypatch):
    monkeypatch.setattr(di, "_gcal_import_events", lambda today: [IMPORT_EV])
    out = di.run_sources(["outlook_calendar"])["outlook_calendar"]
    assert out["status"] == "ok" and out["data"]["cache_status"] == "missing"


def test_outlook_calendar_fresh_cache_skips_import_calendar(cache, monkeypatch):
    monkeypatch.setattr(di, "_gcal_import_events", _no_gcal)
    (cache / "outlook-2026-11-02.json").write_text("[]")
    out = di.run_sources(["outlook_calendar"])["outlook_calendar"]
    assert out["status"] == "ok" and out["data"]["via"] == "janus_cache"


class _FakeCal:
    def __init__(self, items):
        self.items, self.kw = items, None

    def events(self):
        return self

    def list(self, **kw):
        self.kw = kw
        return self

    def execute(self):
        return {"items": self.items}


def test_gcal_import_events_maps_and_windows():
    svc = _FakeCal([
        {"summary": "Daily Scrum", "start": {"dateTime": "2026-11-06T08:35:00-08:00",
                                             "timeZone": "America/Los_Angeles"},
         "end": {"dateTime": "2026-11-06T08:50:00-08:00"}},
        {"summary": "Schnak | OOF", "start": {"date": "2026-11-06"}, "end": {"date": "2026-11-07"}},
        {"summary": "x", "status": "cancelled", "start": {"date": "2026-11-06"}, "end": {}},
    ])
    ev = di._gcal_import_events(dt.date(2026, 11, 6), service=svc)
    assert [e["subject"] for e in ev] == ["Daily Scrum", "Schnak | OOF"]
    assert ev[0]["day"] == "2026-11-06" and ev[1]["all_day"] is True
    assert svc.kw["calendarId"] == di.MSFT_IMPORT_CAL
    assert svc.kw["timeMin"].startswith("2026-11-05T00:00:00")
    assert svc.kw["timeMax"].startswith("2026-11-08T00:00:00")


def test_conflicts_under_z_old_are_excluded(tmp_path):
    v = tmp_path / "vault"
    paths = [str(v / "z_old/a.sync-conflict-1.md"), str(v / "z_old/d/b.sync-conflict-2.md"),
             str(v / "g245/c.sync-conflict-3.md"), str(v / "z_older/d.sync-conflict-4.md")]
    assert di._live_conflicts(paths, v) == paths[2:]


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


# ── google_calendar: all calendars, local-midnight windows ─────────────────

from zoneinfo import ZoneInfo

PST = ZoneInfo("America/Los_Angeles")


class _FakeGc:
    CACHE_DIR = Path("/nonexistent")

    def __init__(self, events, fail=False):
        self.events, self.fail, self.windows = events, fail, []

    def _tz(self):
        return PST

    def _hydrate(self, raw, start, end):
        out = []
        for ev in raw:
            s = dt.datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
            e = dt.datetime.fromisoformat(ev["end"].replace("Z", "+00:00"))
            if e > start and s < end:
                out.append({**ev, "start_dt": s, "end_dt": e})
        return out

    def list_events(self, start, end):
        self.windows.append((start, end))
        if self.fail:
            raise OSError("network")
        return self._hydrate(self.events, start, end)


def _ev(title, cal, start, end, cal_id="x@group.calendar.google.com"):
    return {"title": title, "calendar": cal, "calendar_id": cal_id,
            "start": start, "end": end, "all_day": False, "transparency": "opaque"}


EVENTS = [
    # Sunday 11-01 19:30 PST = 11-02 03:30Z: the old UTC window called it "today".
    _ev("CAIS Basketball (Sun)", "jonathan.b.mckay@gmail.com",
        "2026-11-01T19:30:00-08:00", "2026-11-01T21:45:00-08:00"),
    _ev("GDM interview", "jonathan.b.mckay@gmail.com",
        "2026-11-02T08:00:00-08:00", "2026-11-02T08:45:00-08:00"),
    _ev("r203 Weekly", "m5x2 Cal", "2026-11-02T11:30:00-08:00", "2026-11-02T12:00:00-08:00"),
    _ev("Theo RSM", "3494 House", "2026-11-02T15:30:00-08:00", "2026-11-02T17:05:00-08:00"),
    _ev("SPEAK", "CAIS School", "2026-11-02T19:00:00-08:00", "2026-11-02T20:30:00-08:00"),
    _ev("Dinner", "家 Meals", "2026-11-02T18:30:00-08:00", "2026-11-02T19:00:00-08:00"),
    _ev("Hold", "Calendar", "2026-11-02T16:00:00Z", "2026-11-02T18:00:00Z",
        cal_id="l20n@import.calendar.google.com"),
    _ev("XCORE readout", "jonathan.b.mckay@gmail.com",
        "2026-11-03T14:30:00-08:00", "2026-11-03T15:00:00-08:00"),
]


@pytest.fixture
def monday_3am(monkeypatch):
    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 11, 2, 3, 20, tzinfo=PST).astimezone(tz) if tz else \
                dt.datetime(2026, 11, 2, 3, 20)
    monkeypatch.setattr(di.datetime, "datetime", _DT)


def test_google_calendar_local_today_and_all_calendars(monkeypatch, monday_3am):
    gc = _FakeGc(EVENTS)
    monkeypatch.setattr(di, "_gcal_client", lambda: gc)
    d = di._google_calendar()
    today = [e["summary"] for e in d["today"]]
    assert "CAIS Basketball (Sun)" not in today, "Sunday evening is not Monday"
    assert d["today_date"] == "2026-11-02"
    assert {"jonathan.b.mckay@gmail.com", "m5x2 Cal", "3494 House", "CAIS School",
            "家 Meals"} <= set(d["calendars"])
    assert "Calendar (Outlook ICS mirror)" in d["calendars"]
    assert [e["summary"] for e in d["tomorrow"]] == ["XCORE readout"]
    s, e = gc.windows[0]
    assert s == dt.datetime(2026, 11, 2, tzinfo=PST) and e == dt.datetime(2026, 11, 3, tzinfo=PST)


def test_google_calendar_falls_back_to_janus_cache(tmp_path, monkeypatch, monday_3am):
    gc = _FakeGc([], fail=True)
    gc.CACHE_DIR = tmp_path
    (tmp_path / "gcal-2026-11-02.json").write_text(json.dumps(EVENTS))
    (tmp_path / "gcal-2026-11-03.json").write_text(json.dumps(EVENTS))
    monkeypatch.setattr(di, "_gcal_client", lambda: gc)
    d = di._google_calendar()
    assert d["today_count"] == 6
    assert d["via"]["today"].startswith("janus cache fallback")


def test_gcal_client_hydrate_accepts_trailing_z():
    """The Outlook ICS mirror stores UTC times with 'Z'; python3.9's
    fromisoformat rejects that and _hydrate silently dropped the events."""
    gc = di._gcal_client()
    ev = _ev("Hold", "Calendar", "2026-11-02T16:00:00Z", "2026-11-02T18:00:00Z")
    out = gc._hydrate([ev], dt.datetime(2026, 11, 2, tzinfo=PST),
                      dt.datetime(2026, 11, 3, tzinfo=PST))
    assert len(out) == 1
