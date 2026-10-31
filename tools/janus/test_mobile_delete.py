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
    m._remember_served_event({"id": "abc", "calendar_id": "jm@m5c7.com", "has_guests": False})
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


# ── 2026-10-26 security fix: /api/delete-event is unauthenticated on 0.0.0.0,
# so only ids this server itself served are API-deleted, and only guest-less.
_S, _E = "2026-10-25T09:00:00-07:00", "2026-10-25T09:30:00-07:00"


def test_delete_event_refuses_unserved_id(jm, tmp_path, monkeypatch):
    m, calls = _ev_setup(jm, tmp_path, monkeypatch)
    d = m.app.test_client().post("/api/delete-event", json={
        "title": "Board", "start_iso": _S, "end_iso": _E,
        "event_id": "victim", "calendar_id": "jm@m5c7.com"}).get_json()
    assert d["ok"] and d["mode"] == "hidden" and calls == []


def test_delete_event_with_guests_only_hides(jm, tmp_path, monkeypatch):
    m, calls = _ev_setup(jm, tmp_path, monkeypatch)
    m._remember_served_event({"id": "g", "calendar_id": "jm@m5c7.com", "has_guests": True})
    r = m.delete_calendar_event("Board", _S, _E, "g", "jm@m5c7.com")
    assert r["mode"] == "hidden" and r["reason"] == "has guests" and calls == []


def test_delete_event_unknown_guests_only_hides(jm, tmp_path, monkeypatch):
    """A gcal cache written before has_guests existed → treat as guests."""
    m, calls = _ev_setup(jm, tmp_path, monkeypatch)
    m._remember_served_event({"id": "u", "calendar_id": "jm@m5c7.com"})
    assert m.delete_calendar_event("x", _S, _E, "u", "jm@m5c7.com")["mode"] == "hidden"
    assert calls == []


def test_delete_event_served_id_expires(jm, tmp_path, monkeypatch):
    m, calls = _ev_setup(jm, tmp_path, monkeypatch)
    m._remember_served_event({"id": "a", "calendar_id": "jm@m5c7.com", "has_guests": False})
    m._SERVED_EVENTS[("jm@m5c7.com", "a")] = (False, 0.0)
    assert m.delete_calendar_event("x", _S, _E, "a", "jm@m5c7.com")["mode"] == "hidden"
    assert calls == []


def test_filter_events_registers_served_ids(jm, tmp_path, monkeypatch):
    m, calls = _ev_setup(jm, tmp_path, monkeypatch)
    tz = m._tz()
    s = dt.datetime(2026, 10, 25, 9, 0, tzinfo=tz); e = s + dt.timedelta(minutes=30)
    m._filter_events([{"title": "Focus", "start_dt": s, "end_dt": e, "id": "f",
                       "calendar_id": "jm@m5c7.com", "has_guests": False}], [])
    r = m.delete_calendar_event("Focus", s.isoformat(), e.isoformat(), "f", "jm@m5c7.com")
    assert r["mode"] == "deleted" and calls == [("jm@m5c7.com", "f")]


# ── 2026-10-26: convert_event (swipe right on a past meeting) credits 分 via
# did-fast; its Toggl entry must then count as logged, and re-converting the
# same event must not credit again.
def _convert_setup(jm, monkeypatch):
    today = dt.datetime.now(jm.TZ).date()
    s = dt.datetime.combine(today, dt.time(0, 5), jm.TZ)
    e = s + dt.timedelta(minutes=25)
    runs = []
    monkeypatch.setattr(jm, "_run_did_fast", lambda text, flags=(): runs.append(text) or {
        "ok": True, "step": "5", "tag_steps": [], "needs_agent": False, "stderr_tail": ""})
    created = {"id": 4242, "description": "Sync", "start": s.isoformat(),
               "stop": e.isoformat(), "duration": 1500}
    deleted = []
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [created])
    monkeypatch.setattr(jm.toggl_api, "delete_entry", lambda eid: deleted.append(eid) or True)
    return s, e, runs, deleted


def test_converted_event_entry_cannot_be_swipe_deleted(jm, monkeypatch):
    s, e, runs, deleted = _convert_setup(jm, monkeypatch)
    r = jm.convert_event("Sync", s.isoformat(), e.isoformat(), "i9", True)
    assert r["ok"] and len(runs) == 1
    d = jm.app.test_client().post("/api/delete", json={"id": "4242"}).get_json()
    assert not d["ok"] and "logged" in d["error"] and deleted == []


def test_reconvert_same_event_does_not_double_credit(jm, monkeypatch):
    s, e, runs, _ = _convert_setup(jm, monkeypatch)
    jm.convert_event("Sync", s.isoformat(), e.isoformat(), "i9", True)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [])  # entry gone
    r = jm.convert_event("Sync", s.isoformat(), e.isoformat(), "i9", True)
    assert r["ok"] and r.get("already") and len(runs) == 1


def test_failed_convert_is_not_ledgered(jm, monkeypatch):
    s, e, runs, _ = _convert_setup(jm, monkeypatch)
    monkeypatch.setattr(jm, "_run_did_fast", lambda text, flags=(): runs.append(text) or {
        "ok": False, "step": None, "tag_steps": [], "needs_agent": True, "stderr_tail": ""})
    jm.convert_event("Sync", s.isoformat(), e.isoformat(), "i9", True)
    jm.convert_event("Sync", s.isoformat(), e.isoformat(), "i9", True)
    assert len(runs) == 2 and jm._ledger(s.date()) == {}


# ── 2026-10-27 merge (v169 + v170): a past-day convert credits and ledgers
# under the VIEWED day with --past-ok, records the entry's 分 in the points
# sidecar, and a re-convert from the neighbouring day (_ledger_near) is a no-op.
def test_past_day_convert_ledgers_viewed_day_and_blocks_neighbour_reconvert(jm, monkeypatch):
    day = dt.datetime.now(jm.TZ).date() - dt.timedelta(days=2)
    s = dt.datetime.combine(day, dt.time(23, 40), jm.TZ)
    e = s + dt.timedelta(minutes=40)  # crosses into day+1
    runs = []
    monkeypatch.setattr(jm, "_run_did_fast", lambda text, flags=(): runs.append((text, flags)) or {
        "ok": True, "step": "5", "tag_steps": [], "needs_agent": False, "stderr_tail": "",
        "credits": [{"col": "i9", "points": 40}]})
    created = {"id": 777, "description": "Late", "start": s.isoformat(),
               "stop": e.isoformat(), "duration": 2400}
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [created])
    r = jm.convert_event("Late", s.isoformat(), e.isoformat(), "i9", True, day=day)
    assert r["ok"] and runs[0][1] == ("--past-ok",) and runs[0][0].endswith(f"{day.month}/{day.day}")
    assert "777" in jm._ledger(day)
    assert jm._logged_points(day)["777"]["points"] == 40
    r2 = jm.convert_event("Late", s.isoformat(), e.isoformat(), "i9", True,
                          day=day + dt.timedelta(days=1))
    assert r2.get("already") and len(runs) == 1
