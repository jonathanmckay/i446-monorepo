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

Bug 2 (2026-08-11): the fast-forward left zero record of the stale
occurrence — a days-overdue habit caught up via /did just silently jumped
its due date forward (e.g. xk20 8/12→8/16 in one call) with nothing to show
for it, unlike explicit /defer which always leaves a dated child behind.
Fix: catch_up_recurring now creates a dated, already-closed CHILD copy of
the stale occurrence (reusing defer-fast.py's create_task/
_dated_copy_content) before rescheduling the parent — same shape an
explicit defer leaves, just closed immediately since the habit really was
done. Only the child it creates is ever closed; the parent is only ever
rescheduled, never completed.
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
    # It must reschedule the PARENT via the plain task endpoint, NOT /close
    # (which only advances one interval and can't catch up).
    assert '/tasks/{task_id}",' in body


def test_catch_up_leaves_a_closed_dated_child_not_a_silent_skip():
    body = _func_src("catch_up_recurring")
    # Reuses defer-fast.py's own dated-copy + create helpers rather than
    # reimplementing them, so the child is shaped exactly like an explicit
    # /defer's one-off copy.
    assert "_df._dated_copy_content(content, occurrence)" in body
    assert "_df.create_task(" in body
    # The child is closed — that's the "real completion happened" record.
    assert "close_todoist_task(copy[\"id\"])" in body
    # The parent's own reschedule call must be a bare due_date/due_string
    # POST on task_id, never a close — completing the parent directly would
    # be wrong (it advances via due_date already) and would conflate "the
    # parent was closed" with "the child was closed".
    parent_reschedule = body.split("body = {\"due_date\": target_iso}")[-1]
    assert "close_todoist_task" not in parent_reschedule, (
        "only the child copy may be closed — the parent must only ever be "
        "rescheduled, never completed, even if a stray child from an "
        "earlier explicit defer already exists for this habit")


def test_completion_routes_overdue_daily_recurring_to_catch_up():
    m = _func_src("main")
    assert 'r.todoist_task.get("recurring") and task_due and task_due < today_str' in m, (
        "overdue recurring habits must route to the catch-up branch")
    assert "_is_daily_recurrence(r.todoist_task.get(\"due_string\", \"\"))" in m, (
        "catch-up must be scoped to DAILY recurrences — 'tomorrow' is only the "
        "correct next occurrence for a daily habit")
    # The full task dict is passed through now (not just due_string) so
    # catch_up_recurring has the content/labels/stale due date it needs to
    # build the dated child copy.
    assert "catch_up.append((tid, r.todoist_task))" in m
    assert "catch_up_recurring(" in m
    assert 'content=_task.get("content", "")' in m
    assert 'labels=_task.get("labels", [])' in m
    assert 'stale_due=_task.get("due", "")' in m


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


def test_advance_completion_capped_at_one_day_ahead():
    """ADVANCE_ALLOWED habits (新闻/push/hiit/...) may be completed at most ONE
    day early. Without the cap, each re-complete pushes the recurrence another
    day out and the habit silently drops off dtd's today list (2026-07-14: hiit
    drifted to due+2 and vanished)."""
    m = _func_src("main")
    assert "name_lower in ADVANCE_ALLOWED" in m
    assert "task_due <= tomorrow_str" in m, (
        "advance-completion must be bounded to tomorrow, else recurrence drifts")
    assert "if not advance_ok:" in m
