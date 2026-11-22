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
    assert toggl_store.covers(date(2018, 3, 28), date.today() - timedelta(days=2), db)
    assert not toggl_store.covers(date(2018, 3, 27), date.today() - timedelta(days=2), db)
    assert not toggl_store.covers(date.today() - timedelta(days=2), date.today(), db)


def test_yesterday_goes_live_even_right_after_a_sync(tmp_path, monkeypatch):
    """Yesterday's evening is often backfilled after the 05:10 sync; /0t read
    it from the store on 2026-10-22 and missed 睡觉 21:15-23:59."""
    import toggl_store
    db = _store(tmp_path)  # synced just now
    yesterday = date.today() - timedelta(days=1)
    assert not toggl_store.covers(yesterday, yesterday, db)
    monkeypatch.setattr(toggl_api, "STORE_PATH", db)
    calls = []
    monkeypatch.setattr(toggl_api, "_request", lambda *a, **k: calls.append(a) or [])
    toggl_api.get_entries(yesterday.isoformat(), date.today().isoformat())
    assert len(calls) == 1


# --- dirty days (bug 2026-11-22) -------------------------------------------
# JM filled 19:03-20:30 on a settled day (11/19) from janus. The entry landed
# in Toggl, but every reader kept serving the day from the store, so it looked
# like the write failed; two retries made three copies, since trim_range read
# the same stale store.

def _resync(db, days_back=20, at=None):
    """Re-pull the last `days_back` days, returning the rows already held (an
    empty pull would trip DELETE_GUARD and log the window as incomplete)."""
    t = _tdb(); today = date.today()
    lo = (today - timedelta(days=days_back)).isoformat()
    with t.writer(db) as c:
        cur = c.execute("SELECT * FROM entries WHERE day BETWEEN ? AND ?", (lo, today.isoformat()))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        t.apply_window(c, today - timedelta(days=days_back), today, rows, 1,
                       now=int(at if at is not None else datetime.now().timestamp()))


def test_create_on_a_settled_day_sends_the_next_read_live(tmp_path, monkeypatch):
    db = _store(tmp_path)
    monkeypatch.setattr(toggl_api, "STORE_PATH", db)
    day = date.today() - timedelta(days=3)
    calls = []
    monkeypatch.setattr(toggl_api, "_request", lambda m, p, *a, **k: calls.append((m, p)) or ({"id": 7} if m == "POST" else []))
    monkeypatch.setattr(toggl_api, "_record_create", lambda e: None)
    toggl_api.create_entry("family time", f"{day}T19:03:00-08:00", f"{day}T20:30:00-08:00", 5220)
    toggl_api.get_entries(day.isoformat(), (day + timedelta(days=1)).isoformat())
    assert [m for m, _ in calls] == ["POST", "GET"], "the written day must be read live, not from the store"
    # an untouched settled day is still served locally
    other = day - timedelta(days=4)
    got = toggl_api.get_entries(other.isoformat(), (other + timedelta(days=1)).isoformat())
    assert got and got[0]["_source"] == "store" and len(calls) == 2


def test_trim_range_sees_an_entry_just_created_on_a_settled_day(tmp_path, monkeypatch):
    db = _store(tmp_path)
    monkeypatch.setattr(toggl_api, "STORE_PATH", db)
    monkeypatch.setattr(toggl_api, "_record_create", lambda e: None)
    monkeypatch.setattr(toggl_api, "_notify_tui", lambda: None)
    day = date.today() - timedelta(days=3)
    start = datetime.fromisoformat(f"{day}T19:03:00-08:00"); end = datetime.fromisoformat(f"{day}T20:30:00-08:00")
    live = []

    def fake_request(method, path, body=None, **k):
        if method == "POST":
            e = {"id": 9000 + len(live), "description": body["description"], "start": body["start"],
                 "stop": body["stop"], "duration": body["duration"]}
            live.append(e)
            return e
        return list(live)
    deleted = []
    monkeypatch.setattr(toggl_api, "_request", fake_request)
    monkeypatch.setattr(toggl_api, "delete_entry", lambda i: deleted.append(i) or True)
    toggl_api.create_entry("family time", start.isoformat(), end.isoformat(), 5220)
    toggl_api.trim_range(start, end)                       # the retry's trim
    assert deleted == [9000], "the retry must find and replace the first copy, not stack a duplicate"


def test_a_later_sync_restores_the_store_but_one_inside_the_margin_does_not(tmp_path, monkeypatch):
    import toggl_store
    db = _store(tmp_path, days_ago_synced=0)
    day = date.today() - timedelta(days=3)
    assert toggl_store.mark_dirty(day, db)
    assert not toggl_store.covers(day, day, db)
    now = datetime.now().timestamp()
    _resync(db, at=now + 60)                               # a sync that was mid-flight when the write landed
    assert not toggl_store.covers(day, day, db)
    _resync(db, days_back=1, at=now + 86400)               # a real later sync, but its window misses the day
    assert day in toggl_store.dirty_days(db)
    _resync(db, at=now + 86400)
    assert day not in toggl_store.dirty_days(db)


def test_mark_dirty_is_a_noop_without_a_store_or_for_an_unsettled_day(tmp_path):
    import toggl_store
    missing = tmp_path / "none" / "toggl.sqlite"
    assert not toggl_store.mark_dirty(date.today() - timedelta(days=5), missing)
    db = _store(tmp_path)
    assert not toggl_store.mark_dirty(date.today() - timedelta(days=1), db), "yesterday is read live already"
    assert not list(tmp_path.rglob("toggl-dirty.*.json"))


def test_delete_and_retime_mark_the_stored_day(tmp_path, monkeypatch):
    import toggl_store
    db = _store(tmp_path)
    monkeypatch.setattr(toggl_api, "STORE_PATH", db)
    monkeypatch.setattr(toggl_api, "_request", lambda *a, **k: {"id": 1005, "duration": 60})
    today = date.today()
    toggl_api.update_entry(1005, start=f"{today - timedelta(days=7)}T09:00:00-08:00")   # moves day-5 -> day-7
    assert {today - timedelta(days=5), today - timedelta(days=7)} <= toggl_store.dirty_days(db)
    toggl_api._mark_store_dirty(entry_id=1010)                                          # delete_entry's hook
    assert today - timedelta(days=10) in toggl_store.dirty_days(db)
