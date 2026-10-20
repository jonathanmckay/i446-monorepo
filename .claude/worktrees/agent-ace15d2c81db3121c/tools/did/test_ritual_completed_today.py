"""Regression test: completing a -1neon ritual must record it in completed-today
so dtd hides the card.

Bug (2026-06-29): rituals completed in /inbound (did-fast --ritual <tag>) closed
the Todoist card and stamped the emoji, but never wrote to completed-today.json.
dtd's 'today' cache bucket still held the card, and dtd hides cards via the
completed-today id/name map — so the completed ritual lingered in dtd until a
full cache refresh. Fix: run_ritual appends the closed ritual (keyed by its
Todoist id) to completed-today, which is dtd's hide source.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = (Path(__file__).parent / "did-fast.py").read_text()


def _run_ritual_src() -> str:
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_ritual":
            return ast.get_source_segment(SRC, node)
    raise AssertionError("run_ritual not found in did-fast.py")


def test_run_ritual_records_completed_today_by_id():
    body = _run_ritual_src()
    # Must record the completion via mark-completed...
    assert "mc.append_names" in body, "run_ritual must append the ritual to completed-today"
    # ...keyed by the closed Todoist id (collision-proof, matches dtd's id filter)...
    assert "ids=" in body and "closed_id" in body, \
        "run_ritual must pass the closed task id so dtd hides by id"
    # ...and only after a successful close (guarded by closed_id).
    assert "if closed_id:" in body, "completed-today write must be gated on a successful close"


def test_ritual_dispatch_refreshes_cache():
    """The --ritual CLI path must refresh the task cache after completing, so the
    closed card drops from the 'today' bucket and the cache mtime bump trips
    dtd's auto-reload watcher (the only thing it polls)."""
    tree = ast.parse(SRC)
    main = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert main is not None, "main() not found"
    body = ast.get_source_segment(SRC, main)
    i = body.find('"--ritual"')
    assert i != -1, "--ritual branch not found"
    # within the --ritual branch region, refresh_task_queue must be called
    # (block=True since 2026-06-30 so the cache write completes before return —
    # a backgrounded refresh is torn down and dtd never reloads).
    assert "refresh_task_queue(" in body[i:i + 600], \
        "--ritual must call refresh_task_queue() so dtd auto-reloads"


def test_append_names_id_makes_ritual_hideable(tmp_path):
    """End-to-end at the data layer: an id recorded by append_names lands in the
    id map that dtd reads to hide a cached card."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("mark_completed_r", Path(__file__).parent / "mark-completed.py")
    mc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mc)
    f = tmp_path / "completed-today.json"
    mc.append_names(["😈 -1g"], ids={"😈 -1g": "TID123"}, path=f)
    import json
    data = json.loads(f.read_text())
    # dtd builds completed_ids = set(ids.values()); the card's cache id must be in it.
    assert "TID123" in set(data.get("ids", {}).values())
