"""2026-11-12: JM "swipe left should hide from janis only". The iOS swipe
sends hide_only=true: the Google event survives, only the janus hide key is
written. Default (terminal / web page) still deletes. gcal_client is faked."""
from test_mobile import jm  # noqa: F401

S, E = "2026-11-12T09:00:00-08:00", "2026-11-12T09:30:00-08:00"


def _ev_setup(m, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "HIDDEN_EVENTS", tmp_path / "hidden.json")
    calls = []
    class G:
        @staticmethod
        def delete_event(cal, eid): calls.append((cal, eid))
    monkeypatch.setattr(m, "gcal_client", G)
    return calls


def test_hide_only_never_calls_gcal_and_writes_the_hide_key(jm, tmp_path, monkeypatch):
    calls = _ev_setup(jm, tmp_path, monkeypatch)
    r = jm.delete_calendar_event("Standup", S, E, "abc", "jm@m5c7.com", hide_only=True)
    assert r == {"ok": True, "mode": "hidden"} and calls == []
    assert ("standup", S, E) in jm._hidden_event_keys()
    assert (tmp_path / "hidden.json").exists()


def test_default_still_deletes_google_event(jm, tmp_path, monkeypatch):
    calls = _ev_setup(jm, tmp_path, monkeypatch)
    r = jm.delete_calendar_event("Standup", S, E, "abc", "jm@m5c7.com")
    assert r == {"ok": True, "mode": "deleted"} and calls == [("jm@m5c7.com", "abc")]
    assert ("standup", S, E) in jm._hidden_event_keys()


def test_api_reads_hide_only_from_the_json_body(jm, tmp_path, monkeypatch):
    calls = _ev_setup(jm, tmp_path, monkeypatch)
    c = jm.app.test_client()
    body = {"title": "Standup", "start_iso": S, "end_iso": E, "event_id": "abc", "calendar_id": "jm@m5c7.com"}
    d = c.post("/api/delete-event", json={**body, "hide_only": True}).get_json()
    assert d == {"ok": True, "mode": "hidden"} and calls == []
    d = c.post("/api/delete-event", json={**body, "hide_only": 1}).get_json()
    assert d["mode"] == "hidden" and calls == []
    # absent / false -> the old behaviour (terminal janus, web page)
    d = c.post("/api/delete-event", json=body).get_json()
    assert d["mode"] == "deleted" and calls == [("jm@m5c7.com", "abc")]
    d = c.post("/api/delete-event", json={**body, "hide_only": False}).get_json()
    assert d["mode"] == "deleted" and len(calls) == 2
