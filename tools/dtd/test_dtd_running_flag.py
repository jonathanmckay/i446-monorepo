#!/usr/bin/env python3
"""/api/tasks carries `running` (2026-10-20): the task whose Toggl timer is
live, so the phone can draw ▶ like terminal dtd floats it. Precedence mirrors
dtd.sh: the shared running_timer observation when newer than the hint, else
the dtd-running-hint file (janus /d, dtd Enter) for 4h; an observation that
says nothing is running clears the flag."""
import importlib.util
import time
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_running", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)


def _hint(tmp_path, monkeypatch, name, task_id, age=60):
    f = tmp_path / "dtd-running-hint"
    f.write_text(f"{name}\t{int(time.time()) - age}\t{task_id}")
    monkeypatch.setattr(dtd, "RUNNING_HINT", f)


def test_hint_alone_marks_the_task(tmp_path, monkeypatch):
    _hint(tmp_path, monkeypatch, "ship inkwell", "T1")
    monkeypatch.setattr(dtd, "_running_task", lambda now=None: ("T1", "ship inkwell"))
    assert dtd._running_task() == ("T1", "ship inkwell")


def test_running_task_reads_hint_and_ignores_stale(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "running_timer", type("M", (), {"observed": staticmethod(lambda now=None: None)}))
    _hint(tmp_path, monkeypatch, "ship inkwell", "T1", age=60)
    assert dtd._running_task() == ("T1", "ship inkwell")
    _hint(tmp_path, monkeypatch, "ship inkwell", "T1", age=5 * 3600)
    assert dtd._running_task() == ("", ""), "a hint older than 4h is ignored"


def test_newer_observation_wins_and_empty_clears(tmp_path, monkeypatch):
    import sys
    _hint(tmp_path, monkeypatch, "ship inkwell", "T1", age=600)
    now = time.time()
    obs = {"ts": now - 10, "desc": "coffee", "started": now - 100, "task_id": "T9"}
    monkeypatch.setitem(sys.modules, "running_timer", type("M", (), {"observed": staticmethod(lambda now=None: obs)}))
    assert dtd._running_task() == ("T9", "coffee")
    obs2 = {"ts": now - 10, "desc": "", "started": 0, "task_id": ""}
    monkeypatch.setitem(sys.modules, "running_timer", type("M", (), {"observed": staticmethod(lambda now=None: obs2)}))
    assert dtd._running_task() == ("", ""), "an observation of 'nothing running' clears the hint"
    old_obs = {"ts": now - 2000, "desc": "coffee", "started": 0, "task_id": "T9"}
    monkeypatch.setitem(sys.modules, "running_timer", type("M", (), {"observed": staticmethod(lambda now=None: old_obs)}))
    assert dtd._running_task() == ("T1", "ship inkwell"), "a hint newer than the observation wins"
