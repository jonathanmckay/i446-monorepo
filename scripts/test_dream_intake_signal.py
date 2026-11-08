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
