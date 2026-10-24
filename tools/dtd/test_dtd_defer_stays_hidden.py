#!/usr/bin/env python3
"""Regression (round 3 #4, 2026-10-22): "still seeing delayed tasks in dtd".
A +1d delay from the phone updated Todoist but not dtd.py's cache, so the
next /api/tasks inside the cache window served the old due and the task came
back. defer_task now patches the cached due on success. Also: a daily habit
deferred on another host (its marker mirrored into z_ibx) stays hidden here.
"""
import datetime as dt
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("dtd_defer", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)

TODAY = dt.date.today().isoformat()
TOMORROW = (dt.date.today() + dt.timedelta(days=1)).isoformat()


def _setup(tmp_path, cache):
    cf = tmp_path / "task-queue.json"
    cf.write_text(json.dumps(cache))
    dtd.CACHE = cf
    dtd.DONE_FILE = tmp_path / "completed-today.json"
    dtd.SNOOZE_FILE = tmp_path / "dtd-block-snooze.json"
    dtd.DEFERRED_DIR = tmp_path
    dtd.MIRROR_DIR = tmp_path / "z_ibx"
    dtd.MIRROR_DIR.mkdir()
    dtd._refresh_cache_if_stale = lambda force=False: None


def _task(tid, content, due, **kw):
    t = {"id": tid, "content": content, "labels": [], "priority": 1, "due": due,
         "due_datetime": "", "recurring": False}
    t.update(kw)
    return t


def test_defer_hides_task_before_cache_refresh(tmp_path, monkeypatch):
    _setup(tmp_path, {"updated": dt.datetime.now().isoformat(),
                      "today": [_task("a1", "write memo (30) [10]", TODAY)]})
    assert [t["id"] for t in dtd.build_tasks()] == ["a1"]

    class P:
        returncode = 0
        stdout = json.dumps({"target_date": TOMORROW})
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: P())
    assert dtd.defer_task("a1")["ok"]
    assert dtd.build_tasks() == []


def test_habit_deferred_on_other_host_is_hidden(tmp_path):
    habit = _task("h1", "xk26 (15) [10]", TODAY, recurring=True, labels=["0neon"])
    _setup(tmp_path, {"updated": dt.datetime.now().isoformat(), "0neon": [habit], "today": []})
    assert [t["id"] for t in dtd.build_tasks()] == ["h1"]
    (dtd.MIRROR_DIR / f"habits-deferred-straylight-refit-{TODAY}.ids").write_text("h1\n")
    assert dtd.build_tasks() == []


@pytest.mark.parametrize("section", ["0neon", "夜neon"])
@pytest.mark.parametrize("recurring", [False, True])
def test_daily_tasks_api_uses_same_due_date_cutoff_as_cli(tmp_path, monkeypatch, section, recurring):
    now = dt.datetime.combine(dt.date.fromisoformat(TODAY), dt.time(12), dt.timezone.utc)
    monkeypatch.setattr(dtd, "_now", lambda: now)
    monkeypatch.setattr(dtd, "day_summary", lambda force=False: {"points": 0, "done": 0})
    yesterday = (now.date() - dt.timedelta(days=1)).isoformat()
    tasks = [
        _task("overdue", "overdue habit copy", yesterday, recurring=recurring, labels=[section]),
        _task("today", "today's habit copy", TODAY, recurring=recurring, labels=[section]),
        _task("tomorrow", "delayed habit copy", TOMORROW, recurring=recurring, labels=[section]),
    ]
    _setup(tmp_path, {section: tasks})

    response = dtd.app.test_client().get("/api/tasks")

    assert response.status_code == 200
    assert response.json["ok"]
    assert {t["id"] for t in response.json["tasks"]} == {"overdue", "today"}


@pytest.mark.parametrize("section", ["0neon", "夜neon"])
def test_deferred_daily_copy_returns_only_when_its_due_date_arrives(tmp_path, monkeypatch, section):
    clock = [dt.datetime.combine(dt.date.fromisoformat(TODAY), dt.time(12), dt.timezone.utc)]
    monkeypatch.setattr(dtd, "_now", lambda: clock[0])
    _setup(tmp_path, {section: [
        _task("copy", "deferred habit copy", TOMORROW, labels=[section]),
    ]})

    assert dtd.build_tasks() == []

    clock[0] += dt.timedelta(days=1)
    assert [t["id"] for t in dtd.build_tasks()] == ["copy"]
