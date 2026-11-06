"""v180 (2026-11-06) intake fixes: gmail source names its account (jbm), the
work calendar falls back from Agency to the MSFT Slow Sync import calendar and
then the janus cache, and Syncthing conflicts under z_old/ are not counted."""
import datetime
import importlib.util
import json
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("dream_intake_v180", HERE / "dream-intake.py")
di = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(di)

DAYS = [datetime.date(2026, 11, 5), datetime.date(2026, 11, 6), datetime.date(2026, 11, 7)]
EV = {"subject": "JA: JM 1:1", "start": "2026-11-06T09:30:00", "end": "2026-11-06T10:00:00",
      "organizer": "", "is_online": False}


def test_gmail_source_is_named_for_jbm():
    assert "gmail_inbox" not in di._SOURCES
    assert "gmail_jbm" in di._SOURCES
    assert "jonathan.b.mckay@gmail.com" in di._SOURCES["gmail_jbm"]["desc"]
    assert "NOT m5c7" in di._SOURCES["gmail_jbm"]["desc"]


def test_conflicts_under_z_old_are_excluded(tmp_path):
    v = tmp_path / "vault"
    paths = [str(v / "z_old/a.sync-conflict-1.md"), str(v / "z_old/deep/b.sync-conflict-2.md"),
             str(v / "g245/c.sync-conflict-3.md"), str(v / "z_older/d.sync-conflict-4.md")]
    assert di._live_conflicts(paths, v) == paths[2:]


def _boom(_):
    raise TimeoutError("Agency calendar exceeded 8s")


def test_agency_ok_wins():
    out = di._outlook_calendar(DAYS, agency=lambda d: [EV], gcal=_boom, janus=_boom)
    assert out["via"] == "agency" and out["count"] == 1


def test_agency_timeout_falls_back_to_import_calendar():
    out = di._outlook_calendar(DAYS, agency=_boom, gcal=lambda d: [EV], janus=_boom)
    assert out["via"] == "gcal_import"
    assert out["calendar_id"] == "l20n3a79v2lq68fod4de3lvp1ba2iqft@import.calendar.google.com"
    assert "TimeoutError" in out["fallback_errors"]["agency"]


def test_import_calendar_failure_falls_back_to_janus_cache():
    out = di._outlook_calendar(DAYS, agency=_boom, gcal=_boom,
                               janus=lambda d: ([EV], ["outlook-2026-11-06.json"]))
    assert out["via"] == "janus_cache" and out["files"] == ["outlook-2026-11-06.json"]
    assert set(out["fallback_errors"]) == {"agency", "gcal_import"}


def test_all_sources_fail_raises_with_every_error():
    with pytest.raises(RuntimeError, match="agency.*gcal_import"):
        di._outlook_calendar(DAYS, agency=_boom, gcal=_boom, janus=lambda d: ([], []))


def test_janus_cache_reads_days_and_dedupes_multiday(tmp_path):
    allday = {"subject": "OOF", "start": "2026-11-03T00:00:00.0000000",
              "end": "2026-11-07T00:00:00.0000000", "is_all_day": True}
    (tmp_path / "outlook-2026-11-05.json").write_text(json.dumps([allday]))
    (tmp_path / "outlook-2026-11-06.json").write_text(json.dumps([allday, {**EV}]))
    (tmp_path / "outlook-2026-11-07.json").write_text("{not json")
    events, files = di._janus_cache_events(DAYS, cache_dir=tmp_path)
    assert [e["subject"] for e in events] == ["OOF", "JA: JM 1:1"]
    assert files == ["outlook-2026-11-05.json", "outlook-2026-11-06.json"]
    assert events[0]["is_all_day"] is True


def test_agency_call_is_killed_at_deadline(monkeypatch):
    monkeypatch.setattr(di, "_AGENCY_CAL_CHILD", "import time; time.sleep(30)")
    t0 = time.time()
    with pytest.raises(TimeoutError):
        di._agency_calendar_events(DAYS, deadline=1)
    assert time.time() - t0 < 5


def test_agency_child_output_is_parsed(monkeypatch):
    payload = {"content": [{"text": "Events retrieved successfully.\n" + json.dumps(
        {"value": [{"subject": "Hold", "start": {"dateTime": "2026-11-06T12:00:00"},
                    "end": {"dateTime": "2026-11-06T14:00:00"},
                    "organizer": {"emailAddress": {"name": "JM"}}, "onlineMeeting": {}}]})}]}
    monkeypatch.setattr(di, "_AGENCY_CAL_CHILD",
                        f"import sys; sys.stdout.write({json.dumps(json.dumps(payload))})")
    ev = di._agency_calendar_events(DAYS, deadline=5)
    assert ev == [{"subject": "Hold", "start": "2026-11-06T12:00:00", "end": "2026-11-06T14:00:00",
                   "organizer": "JM", "is_online": False}]


class _FakeEvents:
    def __init__(self, items):
        self.items, self.kw = items, None

    def list(self, **kw):
        self.kw = kw
        return self

    def execute(self):
        return {"items": self.items}


class _FakeService:
    def __init__(self, items):
        self.ev = _FakeEvents(items)

    def events(self):
        return self.ev


def test_gcal_import_maps_events_and_skips_cancelled():
    svc = _FakeService([
        {"summary": "Daily Scrum", "start": {"dateTime": "2026-11-06T08:35:00-08:00"},
         "end": {"dateTime": "2026-11-06T08:50:00-08:00"}},
        {"summary": "Schnak | OOF", "start": {"date": "2026-11-06"}, "end": {"date": "2026-11-07"}},
        {"summary": "gone", "status": "cancelled", "start": {"date": "2026-11-06"}, "end": {}},
    ])
    ev = di._gcal_import_events(DAYS, service=svc)
    assert [e["subject"] for e in ev] == ["Daily Scrum", "Schnak | OOF"]
    assert ev[1]["is_all_day"] is True and ev[0]["is_all_day"] is False
    assert svc.ev.kw["calendarId"] == di.MSFT_IMPORT_CAL
    assert svc.ev.kw["timeMin"].startswith("2026-11-05T00:00:00")
    assert svc.ev.kw["timeMax"].startswith("2026-11-08T00:00:00")
