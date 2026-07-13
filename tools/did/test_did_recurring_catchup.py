#!/usr/bin/env python3
"""Regression: an OVERDUE recurring habit is fast-forwarded on completion, not
just /close'd — so it stops lingering overdue in Todoist mobile.

Bug (2026-07-13): "mobile shows repeated tasks (e.g. 1st/2nd hci) I've already
completed today." A daily-recurring habit ('2nd hci') was stuck overdue at
2026-06-29. Todoist's /close advances a recurrence by ONE interval from the
task's (stale) due date, so an overdue daily habit can never catch up: each
completion moves it +1 day while today also moves +1 day. dtd hides it via its
done-overlay, masking the drift; mobile (no overlay) shows it every day.

Fix: on completion, an overdue recurring task is rescheduled to its next
occurrence (preserving recurrence) instead of a plain /close.
"""
import ast
from pathlib import Path

DIDFAST = Path(__file__).resolve().parent / "did-fast.py"
SRC = DIDFAST.read_text()


def _func_src(name: str) -> str:
    for node in ast.walk(ast.parse(SRC)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"{name} not found")


def test_catch_up_helper_reschedules_preserving_recurrence():
    body = _func_src("catch_up_recurring")
    assert '"due_date": target_iso' in body, "must set a new due date"
    assert 'body["due_string"] = due_string' in body, (
        "must pass the recurrence string so the repeat isn't dropped")
    # It must reschedule via the plain task endpoint, NOT /close (which only
    # advances one interval and can't catch up).
    assert '/tasks/{task_id}",' in body
    assert "/close" not in body.split('"""')[-1], "the code must not hit /close"


def test_completion_routes_overdue_daily_recurring_to_catch_up():
    m = _func_src("main")
    assert 'r.todoist_task.get("recurring") and task_due and task_due < today_str' in m, (
        "overdue recurring habits must route to the catch-up branch")
    assert "_is_daily_recurrence(r.todoist_task.get(\"due_string\", \"\"))" in m, (
        "catch-up must be scoped to DAILY recurrences — 'tomorrow' is only the "
        "correct next occurrence for a daily habit")
    assert 'catch_up.append((tid, r.todoist_task.get("due_string", ""))' in m
    assert "catch_up_recurring(_tid, _due_string, tomorrow_str)" in m, (
        "collected catch-ups must be fast-forwarded to tomorrow")


def test_is_daily_recurrence_scoping():
    fn = _func_src("_is_daily_recurrence")
    # Weekly/monthly must NOT be treated as daily (they'd break cadence).
    assert '"every day" in s' in fn
    assert '"daily" in s' in fn


def test_future_recurring_guard_still_present():
    # Don't regress the existing guard that skips re-closing FUTURE-due recurring
    # tasks (a same-day double-tap must not advance twice).
    m = _func_src("main")
    assert 'if task_due and task_due > today_str:' in m


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
