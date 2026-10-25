"""POST /api/delete (2026-10-25): iOS janus left-swipe delete, mirroring the
terminal janus's ^X on a tracked entry. Toggl is always mocked."""
import datetime as dt

from test_mobile import _raw_cross_midnight, _raw_entry, _raw_running, jm  # noqa: F401


def _wire(jm, monkeypatch, entries):
    deleted = []
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: entries)
    monkeypatch.setattr(jm.toggl_api, "delete_entry", lambda eid: deleted.append(eid) or True)
    return deleted


def test_delete_route_deletes_unlogged_entry(jm, monkeypatch):
    deleted = _wire(jm, monkeypatch, [_raw_entry(jm, 9, 0, 9, 30, desc="coffee", eid=7)])
    d = jm.app.test_client().post("/api/delete", json={"id": "7"}).get_json()
    assert d == {"ok": True, "desc": "coffee"}
    assert deleted == [7]


def test_delete_refuses_running_timer(jm, monkeypatch):
    deleted = _wire(jm, monkeypatch, [_raw_running(jm, 9, 0, eid=7)])
    d = jm.app.test_client().post("/api/delete", json={"id": "7"}).get_json()
    assert not d["ok"] and "running" in d["error"]
    assert deleted == []


def test_delete_refuses_logged_entry(jm, monkeypatch):
    deleted = _wire(jm, monkeypatch, [_raw_entry(jm, 9, 0, 9, 30, eid=7)])
    jm._ledger_add(dt.datetime.now(jm.TZ).date(), "7", "credited")
    d = jm.app.test_client().post("/api/delete", json={"id": "7"}).get_json()
    assert not d["ok"] and "logged" in d["error"]
    assert deleted == []


def test_delete_refuses_cross_midnight(jm, monkeypatch):
    deleted = _wire(jm, monkeypatch, [_raw_cross_midnight(jm, eid=7)])
    d = jm.app.test_client().post("/api/delete", json={"id": "7"}).get_json()
    assert not d["ok"] and "cross-midnight" in d["error"]
    assert deleted == []


def test_delete_missing_entry_and_missing_id(jm, monkeypatch):
    deleted = _wire(jm, monkeypatch, [])
    c = jm.app.test_client()
    assert not c.post("/api/delete", json={"id": "7"}).get_json()["ok"]
    assert c.post("/api/delete", json={}).status_code == 400
    assert deleted == []


# ── calendar events (2026-10-25: "swipe left on calendar events as well to
# delete them"): Google-hosted → API delete + hide; Outlook → hide only.
def _ev_setup(m, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "HIDDEN_EVENTS", tmp_path / "hidden.json")
    calls = []
    class G:
        @staticmethod
        def delete_event(cal, eid): calls.append((cal, eid))
    monkeypatch.setattr(m, "gcal_client", G)
    return m, calls


def test_delete_google_event_calls_api_and_hides(jm, tmp_path, monkeypatch):
    m, calls = _ev_setup(jm, tmp_path, monkeypatch)
    r = m.delete_calendar_event("Standup", "2026-10-25T09:00:00-07:00", "2026-10-25T09:30:00-07:00",
                                "abc", "jm@m5c7.com")
    assert r == {"ok": True, "mode": "deleted"} and calls == [("jm@m5c7.com", "abc")]
    assert ("standup", "2026-10-25T09:00:00-07:00", "2026-10-25T09:30:00-07:00") in m._hidden_event_keys()


def test_delete_outlook_event_only_hides(jm, tmp_path, monkeypatch):
    m, calls = _ev_setup(jm, tmp_path, monkeypatch)
    r = m.delete_calendar_event("1:1", "2026-10-25T10:00:00-07:00", "2026-10-25T10:30:00-07:00",
                                "x", "l20n3a79v2lq68fod4de3lvp1ba2iqft@import.calendar.google.com")
    assert r["mode"] == "hidden" and calls == []


def test_hidden_events_are_filtered_from_the_timeline(jm, tmp_path, monkeypatch):
    import datetime as dt
    m, _ = _ev_setup(jm, tmp_path, monkeypatch)
    tz = m._tz()
    s = dt.datetime(2026, 10, 25, 9, 0, tzinfo=tz); e = s + dt.timedelta(minutes=30)
    raw = [{"title": "Standup", "start_dt": s, "end_dt": e}]
    assert len(m._filter_events(raw, [])) == 1
    m.delete_calendar_event("Standup", s.isoformat(), e.isoformat())
    assert m._filter_events(raw, []) == []
