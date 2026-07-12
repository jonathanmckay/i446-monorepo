#!/usr/bin/env python3
"""Regression: dtd operations act on the task ID, not the (possibly duplicated)
name.

Bug (2026-07-12): a deferred "give kids allowance" kept coming back — two open
tasks shared that exact name (a recurring parent + a stale copy), and every dtd
action resolved the task by *content*, so it either bailed ("multiple matches")
or hit the wrong instance. Every dtd action script already receives the task id
as `$1` (fzf field {2}); the fix threads that id through to each tool:

  defer  → defer-fast  --id "$1"
  points → points-fast --id "$1"
  edit   → edit-fast   --id "$1"
  delete → tid="$1"     (no content re-search)
  done   → FIFO carries "id<TAB>content" → did-fast --task-id "$1"

These pin the wiring (dtd.sh source) plus the tool-level id resolution.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
DTD = (_HERE / "dtd.sh").read_text()
DIDFAST = (_HERE / "did-fast.py").read_text()


# ── dtd.sh wiring: every action passes the id ────────────────────────────────

def test_defer_passes_id():
    assert r'"\$DEFER_FAST" --id "\$1"' in DTD


def test_points_passes_id():
    assert r'"\$POINTS_FAST" --id "\$1"' in DTD


def test_edit_passes_id():
    assert r'"\$EDIT_FAST" --id "\$1"' in DTD


def test_delete_uses_id_not_name_match():
    # The delete script overrides any name-derived tid with the fzf id.
    assert r'tid="\$1"' in DTD


def test_fifo_carries_id_tab_content():
    # enter.sh / done.sh send "id<TAB>content" so the worker can pass --task-id.
    # Heredoc source keeps `\$` escaping and plain quotes.
    assert r"'%s\t%s\n'" in DTD           # tab-separated FIFO format
    assert r'"\$1" "\$clean"' in DTD       # id first, then content


def test_worker_passes_task_id_to_did_fast():
    assert r'"$DID_FAST" --task-id "$task_id"' in DTD


# ── did-fast: completion honours the id ──────────────────────────────────────

def test_did_fast_match_has_preferred_id():
    assert "def match_todoist_task(query: str, tasks: list[dict]," in DIDFAST
    assert "preferred_id: str | None = None" in DIDFAST
    # Early exact-id return before name scoring.
    assert 'if str(task.get("id")) == str(preferred_id):' in DIDFAST


def test_did_fast_main_parses_task_id_and_guards_single_item():
    assert '"--task-id" in argv' in DIDFAST
    # Only applied to a single-item completion (a batch has no single target).
    assert "task_id_override if len(items) == 1 else None" in DIDFAST


def test_route_items_threads_preferred_id():
    assert "preferred_id: str | None = None" in DIDFAST
    assert "preferred_id=preferred_id" in DIDFAST


# ── points-fast: id resolution is exact ──────────────────────────────────────

def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pf():
    return _load("points_fast", "points-fast.py")


def test_resolve_by_id_picks_exact_row_among_duplicates(pf):
    cache = {"today": [
        {"id": "AAA", "content": "give kids allowance (5) [10]"},
        {"id": "BBB", "content": "give kids allowance (5) [10]"},
    ]}
    # Same name twice — by-id must return the exact requested row, where the
    # name-based resolver could only ever return the first.
    assert pf.resolve_by_id(cache, "BBB")["id"] == "BBB"
    assert pf.resolve_by_id(cache, "AAA")["id"] == "AAA"
    assert pf.resolve_by_id(cache, "missing") is None


def test_points_fast_id_flag_in_main_source(pf):
    import inspect
    src = inspect.getsource(pf.main)
    assert '"--id"' in src
    assert "resolve_by_id(cache, task_id)" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
