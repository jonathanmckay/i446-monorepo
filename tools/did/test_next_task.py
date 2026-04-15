"""Regression tests for next-task.py."""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).parent / "next-task.py"
CACHE = Path.home() / "vault/z_ibx/task-queue.json"
COMPLETED = Path.home() / "vault/z_ibx/completed-today.json"


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
