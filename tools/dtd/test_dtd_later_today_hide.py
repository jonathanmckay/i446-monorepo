#!/usr/bin/env python3
"""Regression (wave 2 #4, 2026-10-22): dtd web/phone must hide a task whose
Todoist due carries a time-of-day still ahead of now ("delayed to later
today"). build_tasks sent the time out as `scheduled` and a comment claimed
the client hid it, but nothing — server or client — ever did, so xk87 tasks
deferred to later today kept showing. Now build_tasks hides them until the
time arrives; a date-only due and a timed due already past still show.
"""
import datetime as dt
import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_later", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)

TODAY = dt.date.today().isoformat()


def _setup(tmp_path, cache):
    cf = tmp_path / "task-queue.json"
    cf.write_text(json.dumps(cache))
    dtd.CACHE = cf
    dtd.DONE_FILE = tmp_path / "completed-today.json"
    dtd.SNOOZE_FILE = tmp_path / "dtd-block-snooze.json"
    dtd.DEFERRED_DIR = tmp_path
    dtd._refresh_cache_if_stale = lambda force=False: None


def _iso(h, m=0):
    """A local-zone datetime today at HH:MM as RFC3339 with offset."""
    now = dtd._now()
    return now.replace(hour=h, minute=m, second=0, microsecond=0).isoformat()


def test_timed_due_later_today_is_hidden(tmp_path):
    now = dtd._now()
    # A time safely in the future today (23:59 unless it's the last minute).
    future = _iso(23, 59)
    past = _iso(0, 1)   # 00:01 today — already elapsed for any sane run
    cache = {"updated": now.isoformat(), "0neon": [
        {"id": "1", "content": "xk22 (20) [25]", "due": TODAY, "due_datetime": future,
         "recurring": True, "labels": ["0neon", "xk87"]},
        {"id": "2", "content": "xk20 (20) [25]", "due": TODAY, "due_datetime": past,
         "recurring": True, "labels": ["0neon", "xk87"]},
        {"id": "3", "content": "date-only habit (20) [25]", "due": TODAY, "due_datetime": "",
         "recurring": True, "labels": ["0neon", "xk87"]},
    ]}
    _setup(tmp_path, cache)
    ids = {t["id"] for t in dtd.build_tasks()}
    assert "1" not in ids, "a task scheduled for later today must be hidden until its time"
    assert "2" in ids, "a timed due already past today must still show"
    assert "3" in ids, "a date-only due must never be hidden by the later-today rule"
