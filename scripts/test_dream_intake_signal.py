"""Regression (2026-10-27, Dream v170): intake reported 0 Google events while JM
had a full LA week on the calendar, and `completed_today` served YESTERDAY's
names (completed-today.json is only rewritten on the day's first /did).

google_calendar must cover every account (jbm + m5c7) and every visible
calendar with PT day bounds, dedupe by iCalUID, and carry attendees.
completed_today must come from live Todoist for the PT day.
completed_yesterday_high_value is new: >=500 分 or created+completed same day."""
import datetime
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("dream_intake_sig", HERE / "dream-intake.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------
# Fake Google Calendar service
# --------------------------------------------------------------------------

class _Exec:
    def __init__(self, val):
        self.val = val

    def execute(self):
        if isinstance(self.val, Exception):
            raise self.val
        return self.val


class _FakeService:
    def __init__(self, cals, events_by_cal, calls):
        self.cals, self.events_by_cal, self.calls = cals, events_by_cal, calls

    def calendarList(self):
        svc = self

        class _CL:
            def list(self, **kw):
                return _Exec({"items": svc.cals})
        return _CL()

    def events(self):
        svc = self

        class _EV:
            def list(self, **kw):
                svc.calls.append(kw)
                return _Exec({"items": svc.events_by_cal.get(kw["calendarId"], [])})
        return _EV()


def _ev(uid, summary, start, attendees=None, organizer="x@y.com", **extra):
    e = {"id": uid + "-id", "iCalUID": uid, "summary": summary, "status": "confirmed",
         "start": {"dateTime": start} if "T" in start else {"date": start},
         "end": {"dateTime": start} if "T" in start else {"date": start},
         "organizer": {"email": organizer}}
    if attendees is not None:
        e["attendees"] = attendees
    e.update(extra)
    return e


def _setup_gcal(di, tmp_path, monkeypatch, today):
    tok = tmp_path / "tokens.json"
    tok.write_text(json.dumps({
        "jbm": {"access_token": "a", "refresh_token": "r1"},
        "m5c7": {"access_token": "b", "refresh_token": "r2"},
    }))
    di._CREDS["gcal_tokens_path"] = tok
    di._CREDS["gcal_secrets_path"] = tmp_path / "secrets.json"
    monkeypatch.setattr(di, "_pt_today", lambda: today)

    t = today.isoformat()
    tm = (today + datetime.timedelta(days=1)).isoformat()
    services = {
        "jbm": _FakeService(
            cals=[{"id": "jbm@gmail.com", "summary": "JBM", "primary": True},
                  {"id": "family123@group.calendar.google.com", "summary": "Family"},
                  {"id": "en.usa#holiday@group.v.calendar.google.com", "summary": "Holidays"},
                  {"id": "hidden@group.calendar.google.com", "summary": "Old", "hidden": True}],
            events_by_cal={
                "jbm@gmail.com": [
                    _ev("flight", "UA4699 SNA-SFO", f"{tm}T18:03:00-07:00"),
                    _ev("shared", "Dinner w LX", f"{t}T19:00:00-07:00",
                        attendees=[{"email": "jbm@gmail.com", "self": True, "responseStatus": "accepted"},
                                   {"email": "lx@example.com", "responseStatus": "needsAction"}],
                        organizer="lx@example.com"),
                    _ev("nope", "Declined thing", f"{t}T10:00:00-07:00",
                        attendees=[{"email": "jbm@gmail.com", "self": True, "responseStatus": "declined"}]),
                    # 22:30 PT today = 05:30Z tomorrow: must still be TODAY in PT
                    _ev("late", "Late call", f"{tm}T05:30:00Z"),
                ],
                "family123@group.calendar.google.com": [_ev("pool", "Ren pool", f"{t}T12:30:00-07:00")],
                "en.usa#holiday@group.v.calendar.google.com": [_ev("hol", "Holiday", t)],
                "hidden@group.calendar.google.com": [_ev("hid", "Hidden", t)],
            }, calls=[]),
        "m5c7": _FakeService(
            cals=[{"id": "jm@m5c7.com", "summary": "jm@m5c7.com", "primary": True}],
            events_by_cal={"jm@m5c7.com": [
                _ev("shared", "Dinner w LX", f"{t}T19:00:00-07:00"),  # same iCalUID -> dedupe
                _ev("daily", "Standup", f"{t}T09:00:00-07:00",
                    originalStartTime={"dateTime": f"{t}T09:00:00-07:00"}),
                _ev("daily", "Standup", f"{tm}T09:00:00-07:00",
                    originalStartTime={"dateTime": f"{tm}T09:00:00-07:00"}),
            ]}, calls=[]),
    }
    monkeypatch.setattr(di, "_build_google_creds", lambda *a, account_key=None, **k: account_key)
    monkeypatch.setattr(di, "_google_service", lambda name, ver, creds: services[creds])
    return services


def test_gcal_all_accounts_all_calendars(tmp_path, monkeypatch):
    di = _load()
    today = datetime.date(2026, 10, 27)
    services = _setup_gcal(di, tmp_path, monkeypatch, today)
    out = di._google_calendar()

    today_s = {e["summary"] for e in out["today"]}
    tom_s = {e["summary"] for e in out["tomorrow"]}
    assert "Ren pool" in today_s, "non-'work' secondary calendars must be read"
    assert "UA4699 SNA-SFO" in tom_s, "jbm account must be read"
    assert "Standup" in today_s and "Standup" in tom_s, "recurring instances are distinct"
    assert "Late call" in today_s, "22:30 PT is today in PT, not tomorrow"
    assert "Declined thing" not in today_s
    assert "Holiday" not in today_s and "Hidden" not in today_s
    assert sum(e["summary"] == "Dinner w LX" for e in out["today"]) == 1, "dedupe by iCalUID"
    assert out["today_count"] == len(out["today"]) and out["tomorrow_count"] == len(out["tomorrow"])

    dinner = next(e for e in out["today"] if e["summary"] == "Dinner w LX")
    assert dinner["organizer"] == "lx@example.com"
    assert isinstance(dinner["attendees"], int) and dinner["attendees"] == 2
    assert {"email": "lx@example.com", "responseStatus": "needsAction"} in dinner["attendee_list"]

    assert {a["account"] for a in out["accounts"]} == {"jbm", "m5c7"}
    assert any(c.get("skipped") == "holidays/weeknum" for c in out["calendars"])

    # PT day bounds, not UTC midnight
    call = services["jbm"].calls[0]
    assert call["timeMin"] == "2026-10-27T00:00:00-07:00"
    assert call["timeMax"] == "2026-10-29T00:00:00-07:00"


def test_gcal_one_account_failing_is_reported_not_silent(tmp_path, monkeypatch):
    di = _load()
    today = datetime.date(2026, 10, 27)
    services = _setup_gcal(di, tmp_path, monkeypatch, today)
    services["m5c7"].cals = RuntimeError("invalid_grant")
    out = di._google_calendar()
    assert out["today_count"] >= 1
    assert any(e["account"] == "m5c7" for e in out["errors"])


# --------------------------------------------------------------------------
# Todoist
# --------------------------------------------------------------------------

def _fake_todoist(di, monkeypatch, completed_items, events):
    def fake_http(url, **kw):
        if "/tasks/completed/by_completion_date" in url:
            return {"items": completed_items}
        if "/activities" in url:
            return {"results": events, "next_cursor": None}
        raise AssertionError(url)
    monkeypatch.setattr(di, "_http_request", fake_http)
    monkeypatch.setattr(di, "_todoist_token", lambda: "tok")


def _now_utc(hours_ago=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_completed_yesterday_high_value(monkeypatch):
    di = _load()
    items = [
        {"id": "big", "content": "close refi (90) [800]", "added_at": _now_utc(400),
         "completed_at": _now_utc(5), "due": {"is_recurring": False}, "labels": ["m5x2"]},
        {"id": "small", "content": "email (5) [10]", "added_at": _now_utc(400),
         "completed_at": _now_utc(5), "due": None},
        {"id": "sameday", "content": "fix thing (10) [20]", "added_at": _now_utc(0.5),
         "completed_at": _now_utc(0.1), "due": None},
    ]
    items += [
        {"id": "ritual", "content": "😈 -1t", "added_at": _now_utc(0.5), "completed_at": _now_utc(0.1)},
        {"id": "defer", "content": "deferred: x (5) [5] → 2026-11-02", "added_at": _now_utc(0.5),
         "completed_at": _now_utc(0.1)},
        {"id": "goal", "content": "tasks to 42 {10}", "labels": ["#-1g"], "added_at": _now_utc(0.5),
         "completed_at": _now_utc(0.1)},
    ]
    events = [{"object_id": "hab", "event_date": _now_utc(3),
               "extra_data": {"content": "1 big habit [600]", "is_recurring": True}},
              {"object_id": "hab2", "event_date": _now_utc(3),
               "extra_data": {"content": "0t [50]", "is_recurring": True}}]
    _fake_todoist(di, monkeypatch, items, events)
    out = di._SOURCES["completed_yesterday_high_value"]["fn"]()
    ids = {t["id"]: t for t in out["tasks"]}
    assert set(ids) == {"big", "sameday", "hab"}
    assert ids["big"]["points"] == 800 and "points>=500" in ids["big"]["reasons"]
    assert ids["sameday"]["reasons"] == ["created_and_completed_same_day"]
    assert out["count"] == 3 and out["total_points"] == 800 + 20 + 600


def test_completed_today_is_live_not_yesterdays_file(tmp_path, monkeypatch):
    di = _load()
    import sys
    sys.path.insert(0, str(HERE.parent / "lib"))
    import state_paths
    stale = tmp_path / "completed-today.json"
    stale.write_text(json.dumps({"date": "2026-10-26", "names": ["yesterday thing"]}))
    monkeypatch.setattr(state_paths, "COMPLETED_TODAY", stale)
    monkeypatch.setattr(di, "_pt_today", lambda: datetime.date(2026, 10, 27))
    monkeypatch.setattr(di, "_pt_midnight",
                        lambda d: datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc))
    _fake_todoist(di, monkeypatch, [], [
        {"object_id": "t1", "event_date": _now_utc(1),
         "extra_data": {"content": "wake up [5]", "is_recurring": True}}])
    out = di._completed_today()
    assert out["source"] == "todoist_live" and out["stale"] is False
    assert [t["content"] for t in out["tasks"]] == ["wake up [5]"]
    assert out["file_date"] == "2026-10-26"


def test_completed_today_ignores_wrong_day_file_when_live_fails(tmp_path, monkeypatch):
    di = _load()
    import sys
    sys.path.insert(0, str(HERE.parent / "lib"))
    import state_paths
    stale = tmp_path / "completed-today.json"
    stale.write_text(json.dumps({"date": "2026-10-26", "names": ["yesterday thing"]}))
    monkeypatch.setattr(state_paths, "COMPLETED_TODAY", stale)
    monkeypatch.setattr(di, "_pt_today", lambda: datetime.date(2026, 10, 27))

    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(di, "_http_request", boom)
    monkeypatch.setattr(di, "_todoist_token", lambda: "tok")
    out = di._completed_today()
    assert out["count"] == 0 and out["stale"] is True and "2026-10-26" in out["note"]
