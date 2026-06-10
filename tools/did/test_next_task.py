"""Regression tests for next-task.py."""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).parent / "next-task.py"
sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
import state_paths as _sp  # noqa: E402
CACHE = _sp.TASK_QUEUE
COMPLETED = _sp.COMPLETED_TODAY


def _backup_and_restore(paths):
    """Context manager to backup/restore files."""
    from contextlib import contextmanager

    @contextmanager
    def ctx():
        backups = {}
        for p in paths:
            if p.exists():
                backups[p] = p.read_text()
        try:
            yield
        finally:
            for p in paths:
                if p in backups:
                    p.write_text(backups[p])
                elif p.exists():
                    p.unlink()

    return ctx()


def run_script(*args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True, text=True, timeout=5,
    )
    return result.stdout


def test_filters_future_tasks():
    """Tasks due after today must not appear."""
    today = date.today().isoformat()
    tomorrow = date(2099, 12, 31).isoformat()  # far future

    with _backup_and_restore([CACHE, COMPLETED]):
        CACHE.write_text(json.dumps({
            "refreshed": "2026-01-01T00:00:00",
            "tasks": [
                {"id": "1", "content": "due today [5]", "cat": "0n", "dueDate": today},
                {"id": "2", "content": "due future [5]", "cat": "0n", "dueDate": tomorrow},
            ],
        }))
        COMPLETED.write_text(json.dumps({"date": "1999-01-01", "names": []}))

        out = run_script("something_else")
        assert "due today" in out, f"Today's task should appear. Got: {out}"
        assert "due future" not in out, f"Future task should NOT appear. Got: {out}"


def test_filters_completed_tasks():
    """Tasks in completed-today.json must not appear."""
    today = date.today().isoformat()

    with _backup_and_restore([CACHE, COMPLETED]):
        CACHE.write_text(json.dumps({
            "refreshed": "2026-01-01T00:00:00",
            "tasks": [
                {"id": "1", "content": "hiit (10) [23]", "cat": "0n", "dueDate": today},
                {"id": "2", "content": "push (10) [30]", "cat": "0n", "dueDate": today},
            ],
        }))
        COMPLETED.write_text(json.dumps({"date": today, "names": ["hiit"]}))

        out = run_script("something_else")
        assert "hiit" not in out, f"Completed task should be filtered. Got: {out}"
        assert "push" in out, f"Non-completed task should appear. Got: {out}"


def test_filters_just_completed_habit():
    """The habit passed as argument must not appear."""
    today = date.today().isoformat()

    with _backup_and_restore([CACHE, COMPLETED]):
        CACHE.write_text(json.dumps({
            "refreshed": "2026-01-01T00:00:00",
            "tasks": [
                {"id": "1", "content": "0g - Daily Goals (4) [8]", "cat": "0n", "dueDate": today},
                {"id": "2", "content": "hiit (10) [23]", "cat": "0n", "dueDate": today},
            ],
        }))
        COMPLETED.write_text(json.dumps({"date": "1999-01-01", "names": []}))

        out = run_script("0g")
        assert "0g" not in out, f"Just-completed habit should be filtered. Got: {out}"
        assert "hiit" in out, f"Other task should appear. Got: {out}"


def test_overdue_tasks_appear():
    """Tasks due before today (overdue) should appear."""
    today = date.today().isoformat()

    with _backup_and_restore([CACHE, COMPLETED]):
        CACHE.write_text(json.dumps({
            "refreshed": "2026-01-01T00:00:00",
            "tasks": [
                {"id": "1", "content": "overdue task [10]", "cat": "1n", "dueDate": "2020-01-01"},
            ],
        }))
        COMPLETED.write_text(json.dumps({"date": "1999-01-01", "names": []}))

        out = run_script("something_else")
        assert "overdue task" in out, f"Overdue task should appear. Got: {out}"


def test_execution_speed():
    """Script must complete in under 500ms."""
    import time
    start = time.monotonic()
    run_script("test")
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"Script took {elapsed:.3f}s, must be under 0.5s"


def test_bucketed_cache_shows_all_buckets():
    """Bucketed cache format (0neon, 1neon, today, etc.) must display tasks from ALL buckets.

    Regression: next-task.py read cache.get("tasks", []) which returned [] for
    the bucketed format, showing nothing. After a task deletion triggered
    refresh-cache.py (which dropped the 'today' bucket), only 0neon tasks
    remained visible.
    """
    today = date.today().isoformat()

    with _backup_and_restore([CACHE, COMPLETED]):
        CACHE.write_text(json.dumps({
            "updated": "2026-05-28T08:00:00",
            "0neon": [
                {"id": "a1", "content": "hiit (10) [23]", "labels": ["0neon", "hcbp"],
                 "priority": 3, "due": today},
            ],
            "1neon": [
                {"id": "b1", "content": "1 i9 (10) [40]", "labels": ["1neon", "i9"],
                 "priority": 1, "due": today},
            ],
            "夜neon": [],
            "関键路径": [],
            "today": [
                {"id": "c1", "content": "call dad (20) [20]", "labels": ["s897"],
                 "priority": 1, "due": today},
            ],
        }))
        COMPLETED.write_text(json.dumps({"date": "1999-01-01", "names": []}))

        out = run_script("something_else")
        assert "hiit" in out, f"0neon task should appear. Got: {out}"
        assert "1 i9" in out, f"1neon task should appear. Got: {out}"
        assert "call dad" in out, f"today-bucket task should appear. Got: {out}"


def test_bucketed_cache_deduplicates_by_id():
    """Tasks appearing in both a neon bucket and the today bucket must not be shown twice."""
    today = date.today().isoformat()

    with _backup_and_restore([CACHE, COMPLETED]):
        CACHE.write_text(json.dumps({
            "updated": "2026-05-28T08:00:00",
            "0neon": [
                {"id": "dup1", "content": "hiit (10) [23]", "labels": ["0neon"],
                 "priority": 3, "due": today},
            ],
            "today": [
                {"id": "dup1", "content": "hiit (10) [23]", "labels": ["0neon"],
                 "priority": 3, "due": today},
                {"id": "uniq1", "content": "call dad (20) [20]", "labels": ["s897"],
                 "priority": 1, "due": today},
            ],
            "1neon": [],
            "夜neon": [],
            "関键路径": [],
        }))
        COMPLETED.write_text(json.dumps({"date": "1999-01-01", "names": []}))

        out = run_script("something_else")
        # hiit should appear exactly once
        assert out.count("hiit") == 1, f"Duplicate task should appear once. Got: {out}"
        assert "call dad" in out, f"Unique task should appear. Got: {out}"


def test_bucketed_due_field_filters_future():
    """Bucketed cache uses 'due' field, not 'dueDate'. Future tasks must be filtered."""
    today = date.today().isoformat()

    with _backup_and_restore([CACHE, COMPLETED]):
        CACHE.write_text(json.dumps({
            "updated": "2026-05-28T08:00:00",
            "0neon": [
                {"id": "a1", "content": "hiit (10) [23]", "labels": ["0neon"],
                 "priority": 3, "due": today},
                {"id": "a2", "content": "future task [5]", "labels": ["0neon"],
                 "priority": 3, "due": "2099-12-31"},
            ],
            "1neon": [],
            "夜neon": [],
        }))
        COMPLETED.write_text(json.dumps({"date": "1999-01-01", "names": []}))

        out = run_script("something_else")
        assert "hiit" in out, f"Today's task should appear. Got: {out}"
        assert "future task" not in out, f"Future task should be filtered. Got: {out}"
