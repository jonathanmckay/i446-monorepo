#!/usr/bin/env python3
"""Regression: the mobile dtd server (ix:5560) must hide daily habits already
completed today, even when the completion happened on another machine.

Bug (2026-07-13): "ix:5560 still shows a bunch of tasks I've completed today."
The 0neon/夜neon sections are bounded to due<=tomorrow (to survive a due-date
drift), and done-habit hiding relied on completed-today.json — which is
machine-local. Completions on the Straylight desktop advance each daily habit's
Todoist due date to tomorrow but leave Ix's completed-today.json stale, so all
the completed (now due-tomorrow) habits lingered on mobile.

Fix: a recurring task whose due date has advanced past today is done for today
and is hidden, using the durable Todoist due date carried in the cache rather
than the machine-local completed-today.json.
"""
import datetime as dt
import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_mobile", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)

TODAY = dt.date.today().isoformat()
TOMORROW = (dt.date.today() + dt.timedelta(days=1)).isoformat()
YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _setup(tmp_path, cache, done=None):
    cf = tmp_path / "task-queue.json"
    cf.write_text(json.dumps(cache))
    df = tmp_path / "completed-today.json"
    df.write_text(json.dumps(done or {}))
    dtd.CACHE = cf
    dtd.DONE_FILE = df
    dtd._refresh_cache_if_stale = lambda force=False: None  # no subprocess


def test_completed_daily_habit_advanced_to_tomorrow_is_hidden(tmp_path):
    cache = {"updated": dt.datetime.now().isoformat(), "0neon": [
        {"id": "1", "content": "1st hci (15) [15]", "due": TOMORROW,  "recurring": True, "labels": ["0neon", "hci"]},
        {"id": "2", "content": "2nd hci (15) [15]", "due": TODAY,     "recurring": True, "labels": ["0neon", "hci"]},
        {"id": "3", "content": "stale habit (5) [5]", "due": YESTERDAY, "recurring": True, "labels": ["0neon"]},
    ], "today": [
        {"id": "9", "content": "one-off (10) [10]", "due": TODAY, "recurring": False, "labels": ["i9"]},
    ]}
    _setup(tmp_path, cache)
    ids = {t["id"] for t in dtd.build_tasks()}
    assert "1" not in ids, "daily habit advanced to tomorrow (done today) must be hidden"
    assert "2" in ids, "habit still due today must show"
    assert "3" in ids, "overdue daily habit (needs doing) must show"
    assert "9" in ids, "non-recurring task due today must show"


def test_completed_ids_still_hides_within_ix_window(tmp_path):
    cache = {"updated": dt.datetime.now().isoformat(), "0neon": [
        {"id": "2", "content": "2nd hci (15) [15]", "due": TODAY, "recurring": True, "labels": ["0neon", "hci"]},
    ]}
    done = {"date": TODAY, "ids": {"2nd hci": "2"}}
    _setup(tmp_path, cache, done)
    ids = {t["id"] for t in dtd.build_tasks()}
    assert "2" not in ids, "id in today's completed-today.json must still hide (local window)"


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))


# ── day_summary: header shows real day totals, cross-machine ─────────────────

def test_day_summary_counts_points_and_advanced_habits(tmp_path, monkeypatch):
    cache = {"0neon": [
        {"id": "a", "due": TOMORROW, "recurring": True, "labels": ["0neon"]},   # done today (advanced)
        {"id": "b", "due": TODAY,    "recurring": True, "labels": ["0neon"]},    # still due, not done
    ], "夜neon": [
        {"id": "c", "due": TOMORROW, "recurring": True, "labels": ["夜neon"]},   # done today (advanced)
    ]}
    cf = tmp_path / "task-queue.json"
    cf.write_text(json.dumps(cache))
    dtd.CACHE = cf
    monkeypatch.setattr(dtd, "_todoist_completed_today", lambda: 14)
    import neon.excel as ex
    monkeypatch.setattr(ex, "read", lambda *a, **k: {"ok": True, "value": "664.857"})
    s = dtd.day_summary(force=True)
    assert s["points"] == 664, "points = int(float(0分 Σ))"
    # 14 Todoist completions + 2 daily habits advanced past today (a, c); b not counted
    assert s["done"] == 16


def test_day_summary_degrades_gracefully_when_excel_down(tmp_path, monkeypatch):
    dtd.CACHE = tmp_path / "missing.json"
    monkeypatch.setattr(dtd, "_todoist_completed_today", lambda: 0)
    import neon.excel as ex
    def _boom(*a, **k):
        raise RuntimeError("daemon down")
    monkeypatch.setattr(ex, "read", _boom)
    s = dtd.day_summary(force=True)
    assert s == {"points": 0, "done": 0}, "a dead Excel daemon must not 500 the header"
