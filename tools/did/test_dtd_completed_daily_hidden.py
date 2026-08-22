#!/usr/bin/env python3
"""Regression: a daily/recurring task completed today must not linger in the
refreshed cache's `today` bucket.

Bug (2026-08-21, user report: "dtd showing daily tasks I've already done").
The 2026-08-11 fix computed `closed_today_ids` (ids recorded in
completed-today.json) and filtered them out — but ONLY from `old_today`, the
flaky-fetch fallback. The subsequent final verification pass re-checks live
status for `-1neon` ritual cards ONLY. So a plain daily/recurring task the user
completed today (its id recorded by the completion path) that still comes back
from a fresh, non-empty `fetch_today()` — Todoist's today|overdue index lags
the close by seconds-to-minutes — was written straight into the cache's
`today` bucket, un-filtered, and rendered in dtd.

Fix: after the -1neon verification pass, filter the FINAL `results["today"]`
by `closed_today_ids` too, generalizing the ritual-only guard to every task.

Structural (AST) test rather than a functional one: `fetch_today`/`fetch_label`
are closures inside `_refresh_task_queue_inner`, so exercising the real path
means mocking Todoist's network — flaky and heavy. The defect is purely which
list the existing filter is applied to, so we assert the invariant on the
source: there must be an assignment `results["today"] = <comp filtered by
closed_today_ids>` that lives AFTER the -1neon verification pass (i.e. on the
fresh path), distinct from the `old_today` filter near the top.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "did-fast.py").read_text()
TREE = ast.parse(SRC)


def _assigns_results_today_filtered_by_closed_ids():
    """Yield (lineno) for each `results["today"] = <ListComp>` whose comprehension
    references the name `closed_today_ids`."""
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Assign):
            continue
        # target is results["today"]
        tgt = node.targets[0] if node.targets else None
        if not (isinstance(tgt, ast.Subscript)
                and isinstance(tgt.value, ast.Name) and tgt.value.id == "results"):
            continue
        key = tgt.slice
        if not (isinstance(key, ast.Constant) and key.value == "today"):
            continue
        if not isinstance(node.value, ast.ListComp):
            continue
        names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
        if "closed_today_ids" in names:
            yield node.lineno


def _verification_pass_lineno():
    for node in ast.walk(TREE):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and "-1neon verification pass skipped" in node.value:
            return node.lineno
    raise AssertionError("could not locate the -1neon verification pass marker")


def test_final_today_bucket_is_filtered_by_completed_ids():
    linenos = list(_assigns_results_today_filtered_by_closed_ids())
    assert linenos, (
        "no `results['today'] = [...]` filtered by closed_today_ids found — a "
        "completed daily task can leak into the fresh cache and render in dtd"
    )
    vpass = _verification_pass_lineno()
    assert any(ln > vpass for ln in linenos), (
        "the closed_today_ids filter is only applied on the old_today fallback; "
        "it must also run on the FINAL results['today'] (after the -1neon "
        "verification pass) so a lagging fresh fetch can't resurrect a "
        f"completed daily task (filters at {linenos}, verification pass at {vpass})"
    )


def test_old_today_filter_still_present():
    """Belt-and-suspenders: the original old_today guard must remain too."""
    src_has_old_today = any(
        isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "old_today"
        and isinstance(n.value, ast.ListComp)
        and "closed_today_ids" in {x.id for x in ast.walk(n.value) if isinstance(x, ast.Name)}
        for n in ast.walk(TREE)
    )
    assert src_has_old_today, "the old_today closed_today_ids guard was removed"
