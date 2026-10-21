"""get_entries answers settled past ranges from the local entries store and
goes live for anything touching today (2026-10-21)."""
import importlib.util
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
from toggl_server import toggl_api  # noqa: E402


def _tdb():
    spec = importlib.util.spec_from_file_location("toggl_db", Path.home() / "i446-monorepo/tools/toggl-db/toggl_db.py")
    m = importlib.util.module_from_spec(spec); sys.modules["toggl_db"] = m; spec.loader.exec_module(m)
    return m


def _store(tmp_path, days_ago_synced=0):
    """A store holding one i9 entry on each of the last 20 days, with the
    last completed sync stamped `days_ago_synced` days ago."""
    t = _tdb(); db = tmp_path / "toggl.sqlite"
    today = date.today()
    rows = []
    for i in range(1, 21):
        d = today - timedelta(days=i)
        rows.append({"id": 1000 + i, "day": d.isoformat(), "start": f"{d}T09:00:00-07:00", "stop": f"{d}T10:00:00-07:00",
                     "seconds": 3600, "description": "work", "project_id": 209635316, "project": "i9",
                     "tag_ids": "[11069640]", "tags": '["-1"]', "at": None, "fetched_at": 1})
    synced = int(datetime.now().timestamp()) - days_ago_synced * 86400
    with t.writer(db) as c:
        t.apply_window(c, today - timedelta(days=20), today, rows, 1, now=synced)
    return db


def test_past_range_is_served_from_the_store_without_a_request(tmp_path, monkeypatch):
    db = _store(tmp_path)
    monkeypatch.setattr(toggl_api, "STORE_PATH", db)
    monkeypatch.setattr(toggl_api, "_request", lambda *a, **k: pytest.fail("went live for a settled range"))
    today = date.today()
    got = toggl_api.get_entries((today - timedelta(days=8)).isoformat(), (today - timedelta(days=1)).isoformat())
    assert len(got) == 7 and got[0]["_source"] == "store"
    e = got[0]
    assert e["duration"] == 3600 and e["project_id"] == 209635316 and e["tags"] == ["-1"] and e["tag_ids"] == [11069640]
    assert e["start"].startswith((today - timedelta(days=8)).isoformat())


def test_range_touching_today_or_unsettled_days_goes_live(tmp_path, monkeypatch):
    db = _store(tmp_path)
    monkeypatch.setattr(toggl_api, "STORE_PATH", db)
    calls = []
    monkeypatch.setattr(toggl_api, "_request", lambda *a, **k: calls.append(a) or [])
    today = date.today()
    toggl_api.get_entries((today - timedelta(days=3)).isoformat(), (today + timedelta(days=1)).isoformat())
    assert len(calls) == 1, "today is never settled"
    stale = _store(tmp_path / "stale", days_ago_synced=5)          # last sync 5 days ago: yesterday not settled
    monkeypatch.setattr(toggl_api, "STORE_PATH", stale)
    toggl_api.get_entries((today - timedelta(days=3)).isoformat(), (today).isoformat())
    assert len(calls) == 2


def test_no_store_and_forced_live_both_go_live(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(toggl_api, "_request", lambda *a, **k: calls.append(a) or [])
    monkeypatch.setattr(toggl_api, "STORE_PATH", tmp_path / "missing.sqlite")
    today = date.today()
    toggl_api.get_entries((today - timedelta(days=8)).isoformat(), (today - timedelta(days=1)).isoformat())
    assert len(calls) == 1
    monkeypatch.setattr(toggl_api, "STORE_PATH", _store(tmp_path))
    monkeypatch.setenv("TOGGL_LIVE", "1")
    toggl_api.get_entries((today - timedelta(days=8)).isoformat(), (today - timedelta(days=1)).isoformat())
    assert len(calls) == 2


def test_store_covers_rejects_days_before_the_first_entry(tmp_path):
    import toggl_store
    db = _store(tmp_path)
    assert toggl_store.covers(date(2018, 3, 28), date.today() - timedelta(days=1), db)
    assert not toggl_store.covers(date(2018, 3, 27), date.today() - timedelta(days=1), db)
    assert not toggl_store.covers(date.today() - timedelta(days=2), date.today(), db)
