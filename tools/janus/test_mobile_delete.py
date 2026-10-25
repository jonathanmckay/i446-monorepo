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
