#!/usr/bin/env python3
"""Regression: a daily/recurring task completed today — on ANY host — must not
linger in the refreshed cache's rendered buckets.

Bug (2026-08-21, user report: "dtd showing daily tasks I've already done",
completed in dtd on Straylight while viewing dtd web served from ix). Two gaps:

  1. `closed_today_ids` (ids in completed-today.json) was filtered out ONLY from
     `old_today` (the flaky-fetch fallback); the final verification pass
     re-checks live status for `-1neon` ritual cards ONLY. So a plain
     daily/recurring habit that a fresh, non-empty fetch still returned (Todoist
     today|overdue index lag, or a 0neon/夜neon label bucket holding a recurring
     card whose due only just advanced) was written into the cache unfiltered.
     A recurring task is never marked `checked` on close — its due just advances
     — so neither the id-hide nor the due-hide caught it.
  2. completed-today.json is PER-HOST. A task closed on Straylight was invisible
     to ix's refresh, so ix's `closed_today_ids` never contained it.

Fix:
  - Call `mc.absorb_remote()` at the top of `_refresh_task_queue_inner`, before
    `closed_today_ids` is computed, so another host's mirrored completions merge
    in first (the designed cross-host reconciliation, previously only triggered
    by dtd's watcher).
  - Filter EVERY rendered list bucket in `results` (today, 0neon, 夜neon, …) by
    `closed_today_ids` after the -1neon verification pass, not just `today`.

Structural (AST) test rather than functional: `fetch_today`/`fetch_label` are
closures inside `_refresh_task_queue_inner`, so the real path means mocking
Todoist's network — flaky and heavy. The defect is which list(s) the filter is
applied to and whether the cross-host merge runs, both checkable on the source.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "did-fast.py").read_text()
TREE = ast.parse(SRC)


def _inner_fn():
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == "_refresh_task_queue_inner":
            return node
    raise AssertionError("_refresh_task_queue_inner not found")


def _results_bucket_assigns_filtered_by_closed_ids():
    """Yield lineno for each `results[<expr>] = <ListComp>` whose comprehension
    references `closed_today_ids` (covers both a `today`-literal key and the
    all-buckets loop form `results[_bk] = [...]`)."""
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        tgt = node.targets[0]
        if not (isinstance(tgt, ast.Subscript)
                and isinstance(tgt.value, ast.Name) and tgt.value.id == "results"):
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


def test_rendered_buckets_filtered_by_completed_ids_on_fresh_path():
    linenos = list(_results_bucket_assigns_filtered_by_closed_ids())
    assert linenos, (
        "no `results[...] = [...]` filtered by closed_today_ids found — a "
        "completed daily task can leak into the fresh cache and render in dtd"
    )
    vpass = _verification_pass_lineno()
    assert any(ln > vpass for ln in linenos), (
        "the closed_today_ids filter is only applied on the old_today fallback; "
        "it must also run on the FINAL results buckets (after the -1neon "
        "verification pass) so a lagging/label-bucket fresh fetch can't "
        f"resurrect a completed daily task (filters at {linenos}, pass at {vpass})"
    )


def test_daily_habit_buckets_not_only_today_are_covered():
    """The filter must reach the 0neon/夜neon daily-habit buckets, not just
    `today` — the reported items are habits, which live in those buckets. The
    all-buckets loop form (subscript key is a Name, not the 'today' literal)
    satisfies this; a today-only literal filter alone does not."""
    covers_non_today = False
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        tgt = node.targets[0]
        if not (isinstance(tgt, ast.Subscript)
                and isinstance(tgt.value, ast.Name) and tgt.value.id == "results"):
            continue
        if not isinstance(node.value, ast.ListComp):
            continue
        names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
        if "closed_today_ids" not in names:
            continue
        key = tgt.slice
        # A non-constant key (e.g. loop var _bk) means it filters every bucket.
        if not (isinstance(key, ast.Constant) and key.value == "today"):
            covers_non_today = True
    assert covers_non_today, (
        "closed_today_ids only filters results['today']; daily-habit buckets "
        "(0neon / 夜neon) are left unfiltered and completed habits still render"
    )


def test_absorb_remote_called_in_inner_refresh():
    """Cross-host: the inner refresh must merge other hosts' completions before
    building the cache, or a task done on Straylight stays visible on ix."""
    inner = _inner_fn()
    called = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "absorb_remote"
        for n in ast.walk(inner)
    )
    assert called, (
        "mc.absorb_remote() is not called inside _refresh_task_queue_inner; "
        "cross-host completions never reach ix's closed_today_ids"
    )
