#!/usr/bin/env python3
"""Regression (2026-10-23): "when recurring task in dtd gets marked with an
abacus, the recurrance should not also have an abacus". The running timer was
matched by id OR name, so a recurring card and its same-named one-off copy
both showed ▶/🧮. Now: id match only when the timer has an id; otherwise the
first visible same-named card."""
import datetime as dt
import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_run1", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)
TODAY = dt.date.today().isoformat()


def _setup(tmp_path, monkeypatch, running):
    cache = {"updated": dt.datetime.now().isoformat(), "today": [
        {"id": "parent", "content": "give kids allowance (5) [10]", "labels": [], "priority": 1,
         "due": TODAY, "due_datetime": "", "recurring": True},
        {"id": "copy", "content": "give kids allowance (5) [10]", "labels": [], "priority": 1,
         "due": TODAY, "due_datetime": "", "recurring": False},
    ]}
    cf = tmp_path / "task-queue.json"; cf.write_text(json.dumps(cache))
    monkeypatch.setattr(dtd, "CACHE", cf)
    monkeypatch.setattr(dtd, "DONE_FILE", tmp_path / "done.json")
    monkeypatch.setattr(dtd, "SNOOZE_FILE", tmp_path / "snooze.json")
    monkeypatch.setattr(dtd, "DEFERRED_DIR", tmp_path)
    monkeypatch.setattr(dtd, "MIRROR_DIR", tmp_path)
    monkeypatch.setattr(dtd, "_refresh_cache_if_stale", lambda force=False: None)
    monkeypatch.setattr(dtd, "_running_task", lambda now=None: running)


def test_timer_with_id_marks_only_that_card(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, ("copy", "give kids allowance"))
    assert [t["id"] for t in dtd.build_tasks() if t["running"]] == ["copy"]


def test_timer_without_id_marks_one_same_named_card(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, ("", "give kids allowance"))
    assert len([t for t in dtd.build_tasks() if t["running"]]) == 1


def test_cli_list_marks_one_running_card():
    src = (Path(__file__).parent.parent / "did" / "dtd.sh").read_text()
    assert "_run_taken[0] = _run_taken[0] or is_running" in src
    assert "is_running = bool(running_clean and clean == running_clean)\n" not in src
