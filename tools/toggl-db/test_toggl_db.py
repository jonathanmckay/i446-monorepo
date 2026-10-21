#!/usr/bin/env python3
"""toggl_db: the SQLite store of Toggl entries (2026-10-21)."""
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
import importlib.util

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("toggl_db", HERE / "toggl_db.py")
    m = importlib.util.module_from_spec(spec); sys.modules["toggl_db"] = m; spec.loader.exec_module(m)
    return m


def _row(i, day, secs, code="i9", start=None):
    return {"id": i, "day": day, "start": start or f"{day}T09:00:00-07:00", "stop": None, "seconds": secs,
            "description": "x", "project_id": 1, "project": code, "tag_ids": "[]", "tags": "[]", "at": None, "fetched_at": 1}


def test_writer_is_atomic_and_leaves_no_wal(tmp_path):
    t = _load(); db = tmp_path / "t.sqlite"
    with t.writer(db) as c:
        t.apply_window(c, date(2026, 10, 1), date(2026, 10, 2), [_row(1, "2026-10-01", 600)], 1, now=5)
    assert db.exists() and not list(tmp_path.glob("*.tmp-*")) and not (tmp_path / "t.sqlite-wal").exists()
    try:
        with t.writer(db) as c:
            c.execute("DELETE FROM entries"); raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not list(tmp_path.glob("*.tmp-*"))
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1, "a failed write must not touch the real file"


def test_apply_window_upserts_and_deletes_only_inside_the_window(tmp_path):
    t = _load(); db = tmp_path / "t.sqlite"
    with t.writer(db) as c:
        t.apply_window(c, date(2026, 10, 1), date(2026, 10, 7), [_row(1, "2026-10-01", 600), _row(2, "2026-10-03", 300)], 1, now=5)
        t.apply_window(c, date(2026, 10, 8), date(2026, 10, 14), [_row(3, "2026-10-09", 100)], 1, now=5)
    with t.writer(db) as c:   # entry 2 vanished in Toggl, entry 1 was edited
        n, gone, complete = t.apply_window(c, date(2026, 10, 1), date(2026, 10, 7), [_row(1, "2026-10-01", 900)], 1, now=6)
    assert (n, gone, complete) == (1, 1, 1)
    rows = sqlite3.connect(db).execute("SELECT id, seconds FROM entries ORDER BY id").fetchall()
    assert rows == [(1, 900), (3, 100)], "row 3 is outside the window and must survive"


def test_daily_totals_and_export_shape_with_explicit_empty_days(tmp_path):
    t = _load(); db = tmp_path / "t.sqlite"
    with t.writer(db) as c:
        t.apply_window(c, date(2026, 10, 1), date(2026, 10, 3),
                       [_row(1, "2026-10-01", 600), _row(2, "2026-10-01", 90, "xk87"), _row(3, "2026-10-03", 3600)], 1, now=5)
    conn = t.connect_ro(db)
    d = t.daily_totals(conn)
    assert d["2026-10-01"] == {"minutes": {"i9": 10, "xk87": 1}, "entries": {"i9": 1, "xk87": 1}}
    assert d["2026-10-02"] == {"minutes": {}, "entries": {}}, "a day with no entries is present, not missing"
    out = tmp_path / "cache.json"
    assert t.export_cache(conn, out) == 3 and json.loads(out.read_text())["2026-10-03"]["minutes"] == {"i9": 60}
    assert "2026-10-02" in t.status(conn)


def test_local_day_buckets_by_home_tz():
    t = _load()
    assert t.local_day("2026-10-20T06:30:00+00:00") == "2026-10-19"   # 23:30 PDT the evening before
    assert t.local_day("2026-10-19T00:00:00-07:00") == "2026-10-19"


def test_fetch_window_sends_large_page_follows_full_cursor_and_fails_loud():
    t = _load()
    calls = []
    def post(url, body, auth):
        calls.append(body)
        if "first_id" not in body:
            return ({"x-next-id": "9", "x-next-row-number": "2", "x-next-timestamp": "77"},
                    [{"project_id": 1, "description": "a", "tag_ids": [5],
                      "time_entries": [{"id": 1, "seconds": 60, "start": "2026-10-19T09:00:00-07:00", "stop": None, "at": None},
                                       {"id": 2, "seconds": -1, "start": "2026-10-19T10:00:00-07:00"},      # running: skipped
                                       {"id": 3, "seconds": 60, "start": "2026-10-25T09:00:00-07:00"}]}])  # outside window: skipped
        return ({}, [{"project_id": None, "description": "b", "tag_ids": [],
                      "time_entries": [{"id": 4, "seconds": 120, "start": "2026-10-18T09:00:00-07:00"}]}])
    rows, pages = t.fetch_window("auth", date(2026, 10, 18), date(2026, 10, 19), {1: "i9"}, {"5": "-1"}, post=post, sleep=lambda s: None, log=lambda m: None)
    assert pages == 2 and [r["id"] for r in rows] == [1, 4]
    assert calls[0]["page_size"] == t.PAGE_SIZE
    assert (calls[0]["start_date"], calls[0]["end_date"]) == ("2026-10-17", "2026-10-20"), "one-day pad each side"
    assert calls[1]["first_id"] == 9 and calls[1]["first_row_number"] == 2 and calls[1]["first_timestamp"] == 77
    assert rows[0]["project"] == "i9" and rows[0]["tags"] == '["-1"]' and rows[1]["project"] == t.NO_PROJECT
    import urllib.error
    def bad(url, body, auth): raise urllib.error.URLError("down")
    try:
        t.fetch_window("auth", date(2026, 10, 18), date(2026, 10, 19), {}, {}, post=bad, sleep=lambda s: None, log=lambda m: None)
        assert False, "must raise"
    except t.FetchError:
        pass


def test_short_response_keeps_stored_rows_and_marks_window_incomplete(tmp_path):
    t = _load(); db = tmp_path / "t.sqlite"
    with t.writer(db) as c:
        t.apply_window(c, date(2026, 10, 1), date(2026, 10, 7), [_row(i, "2026-10-02", 60) for i in range(1, 11)], 1, now=5)
    with t.writer(db) as c:   # a spurious empty 200: nothing may be deleted
        n, gone, complete = t.apply_window(c, date(2026, 10, 1), date(2026, 10, 7), [], 1, now=6)
    assert (n, gone, complete) == (0, 0, 0)
    with t.writer(db) as c:   # 3 of 10 is under the guard too
        n, gone, complete = t.apply_window(c, date(2026, 10, 1), date(2026, 10, 7), [_row(i, "2026-10-02", 60) for i in (1, 2, 3)], 1, now=7)
    assert (n, gone, complete) == (3, 0, 0)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 10
    with t.writer(db) as c:   # 6 of 10 clears the guard: the other four really are gone
        n, gone, complete = t.apply_window(c, date(2026, 10, 1), date(2026, 10, 7), [_row(i, "2026-10-02", 60) for i in range(1, 7)], 1, now=8)
    assert (n, gone, complete) == (6, 4, 1)


def test_429_waits_for_the_reset_and_retries_once():
    t = _load()
    import urllib.error, email.message
    calls, slept = [], []
    def post(url, body, auth):
        calls.append(1)
        if len(calls) == 1:
            h = email.message.Message(); h["Retry-After"] = "7"
            raise urllib.error.HTTPError(url, 429, "quota", h, None)
        return ({}, [{"project_id": 1, "description": "a", "tag_ids": [], "time_entries": [{"id": 1, "seconds": 60, "start": "2026-10-19T09:00:00-07:00"}]}])
    rows, pages = t.fetch_window("auth", date(2026, 10, 19), date(2026, 10, 19), {1: "i9"}, {}, post=post, sleep=slept.append, log=lambda m: None)
    assert [r["id"] for r in rows] == [1] and slept == [9] and len(calls) == 2


def test_quota_floor_sleeps_until_the_reset():
    t = _load(); slept = []
    def post(url, body, auth):
        return ({"x-toggl-quota-remaining": "3", "x-toggl-quota-resets-in": "40"}, [])
    t.fetch_window("auth", date(2026, 10, 19), date(2026, 10, 19), {}, {}, post=post, sleep=slept.append, log=lambda m: None)
    assert slept == [42]


def test_sync_range_writes_each_window_atomically_and_logs(tmp_path):
    t = _load(); db = tmp_path / "t.sqlite"
    fetched = []
    def fetch(s, e):
        fetched.append((s, e)); return [_row(int(s.strftime("%d")), s.isoformat(), 60)], 1
    n = t.sync_range(date(2026, 10, 1), date(2026, 10, 10), window=4, path=db, fetch=fetch, log=lambda m: None)
    assert n == 3 and fetched == [(date(2026, 10, 1), date(2026, 10, 4)), (date(2026, 10, 5), date(2026, 10, 8)), (date(2026, 10, 9), date(2026, 10, 10))]
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM sync_log WHERE complete=1").fetchone()[0] == 3


def test_verify_tolerates_sub_minute_rounding(tmp_path):
    t = _load(); db = tmp_path / "t.sqlite"
    from datetime import timedelta
    yday = (date.today() - timedelta(days=1)).isoformat()   # verify never looks at today (still being tracked)
    with t.writer(db) as c:
        t.apply_window(c, date.fromisoformat(yday), date.fromisoformat(yday), [_row(1, yday, 600)], 1, now=5)
    live = [{"id": 1, "duration": 630, "start": f"{yday}T09:00:00-07:00"}, {"id": 2, "duration": 999, "start": f"{date.today()}T09:00:00-07:00"}]
    assert t.verify(t.connect_ro(db), "auth", 1, get=lambda u, a: live) == []
    live[0]["duration"] = 700
    assert t.verify(t.connect_ro(db), "auth", 1, get=lambda u, a: live) == [f"{yday}: db 10m vs live 11m"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
