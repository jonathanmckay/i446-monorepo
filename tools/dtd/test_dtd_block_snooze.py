#!/usr/bin/env python3
"""Regression: dtd web (ix:5560) must honor the same "delay to later today"
state the desktop dtd fzf TUI writes, so a task snoozed/deferred there
doesn't reappear immediately in the web view.

Bug (2026-08-11): "in dtd web, tasks that I've delayed to later in the day
are still showing up." Desktop dtd's ctrl-v block-snooze writes
~/.local/state/jm/dtd-block-snooze.json ({date, snoozes: {id: start_hour}}),
and /defer'ing a daily habit (0neon/夜neon) writes its parent id into
~/.cache/jm/habits-deferred-<date>.ids — tools/did/dtd.sh reads both to hide
those tasks until their block hour arrives / for the rest of the day. dtd web
read neither file, so the exact same task stayed hidden in the terminal but
reappeared on the phone.

Fix: build_tasks() now excludes ids in both files, mirroring dtd.sh's own
read/filter logic (same date-gating: a stale date from a previous day voids
the file, same as dtd.sh).
"""
import datetime as dt
import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_snooze", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)

TODAY = dt.date.today().isoformat()
YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _setup(tmp_path, cache):
    cf = tmp_path / "task-queue.json"
    cf.write_text(json.dumps(cache))
    dtd.CACHE = cf
    dtd.DONE_FILE = tmp_path / "completed-today.json"  # absent → no completions
    dtd.SNOOZE_FILE = tmp_path / "dtd-block-snooze.json"
    dtd.DEFERRED_DIR = tmp_path
    dtd._refresh_cache_if_stale = lambda force=False: None  # no subprocess


def test_block_snoozed_id_hidden_until_its_hour(tmp_path):
    cache = {"updated": dt.datetime.now().isoformat(), "today": [
        {"id": "1", "content": "delayed task (10) [5]", "due": TODAY, "recurring": False, "labels": ["i9"]},
        {"id": "2", "content": "normal task (10) [5]", "due": TODAY, "recurring": False, "labels": ["i9"]},
    ]}
    _setup(tmp_path, cache)
    # 99 is always > any real hour-of-day (0-23) — a snooze target guaranteed
    # to still be in the future regardless of when this test runs, mirroring
    # the code's bare `now_hour < int(v)` comparison (no upper bound check).
    dtd.SNOOZE_FILE.write_text(json.dumps({"date": TODAY, "snoozes": {"1": 99}}))
    ids = {t["id"] for t in dtd.build_tasks()}
    assert "1" not in ids, "block-snoozed id must stay hidden in dtd web, same as desktop dtd"
    assert "2" in ids, "an un-snoozed task must still show"


def test_stale_snooze_date_is_ignored(tmp_path):
    cache = {"updated": dt.datetime.now().isoformat(), "today": [
        {"id": "1", "content": "task (10) [5]", "due": TODAY, "recurring": False, "labels": ["i9"]},
    ]}
    _setup(tmp_path, cache)
    dtd.SNOOZE_FILE.write_text(json.dumps({"date": YESTERDAY, "snoozes": {"1": 23}}))
    ids = {t["id"] for t in dtd.build_tasks()}
    assert "1" in ids, "a snooze file left over from a previous day must not hide today's task"


def test_deferred_daily_habit_hidden_for_rest_of_day(tmp_path):
    cache = {"updated": dt.datetime.now().isoformat(), "0neon": [
        {"id": "3", "content": "xk22 (20) [25]", "due": TODAY, "recurring": True, "labels": ["0neon", "xk87"]},
        {"id": "4", "content": "xk20 (20) [25]", "due": TODAY, "recurring": True, "labels": ["0neon", "xk87"]},
    ]}
    _setup(tmp_path, cache)
    (tmp_path / f"habits-deferred-{TODAY}.ids").write_text("3\n")
    ids = {t["id"] for t in dtd.build_tasks()}
    assert "3" not in ids, "a /defer'd daily habit must stay hidden in dtd web today"
    assert "4" in ids, "a non-deferred daily habit must still show"


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
