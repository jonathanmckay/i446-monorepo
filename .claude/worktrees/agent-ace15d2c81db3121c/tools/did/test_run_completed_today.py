"""Regression test for run.py's completed-today bookkeeping.

Bug (2026-08-02, user report: "a ton of 0neon tasks that I already marked as
done today are back" — 0l, xk20, xk22, ...): run.py had its own private
_append_completed() that wrote ~/.local/state/jm/completed-today.json
directly — no fcntl lock (unlike mark-completed.py's append_names, used by
every other completion path on the same file) and no "ids" map at all. Two
consequences:

  1. Racing against a concurrent locked writer (did-fast.py's ritual/general
     dispatch, or absorb_remote()'s cross-machine merge) could silently lose
     run.py's just-recorded completion on the next unlocked read-modify-write.
  2. Even without a race, habits closed via run.py (0l, xk20/22/26, 冥想,
     o314, hiit, ...) never got their Todoist id recorded, so dtd's id-based
     _completed_ids() guard (dtd.py: "Hiding is by id ONLY, never by name")
     could never suppress them — leaving the due-date guard as the *only*
     defense during the window before the next task-queue.json refresh, in
     which a just-completed recurring habit still shows the stale
     due=today snapshot and reappears in dtd.

Fix: run.py now calls the shared, locked, atomic mark-completed.py
append_names() (same module did-fast.py uses), threading the closed task's
Todoist id through from _find_and_close_todoist.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

_RUN_SPEC = importlib.util.spec_from_file_location("did_run_ct", _HERE / "run.py")
run = importlib.util.module_from_spec(_RUN_SPEC)
sys.modules["did_run_ct"] = run
_RUN_SPEC.loader.exec_module(run)  # type: ignore[union-attr]


def test_append_completed_delegates_to_shared_locked_writer():
    """_append_completed must call mc.append_names, not write the file itself."""
    with patch.object(run.mc, "append_names") as fake_append:
        run._append_completed("0l", "6ghxxj7hhCWQgvqc")
    fake_append.assert_called_once_with(
        ["0l"], ids={"0l": "6ghxxj7hhCWQgvqc"}
    )


def test_append_completed_omits_ids_when_no_task_id():
    with patch.object(run.mc, "append_names") as fake_append:
        run._append_completed("some habit", None)
    fake_append.assert_called_once_with(["some habit"], ids=None)


def test_find_and_close_todoist_returns_id():
    """The id is needed by callers to record completed-today's id map —
    losing it (returning bare content, the pre-fix signature) is exactly how
    xk20/xk22/0l lost id-based dtd protection."""
    fake_task = {"id": "TID123", "content": "xk20 (30) [35]"}
    with patch.object(run.todoist, "find_tasks", return_value=[fake_task]), \
         patch.object(run.todoist, "close_task"), \
         patch.object(run, "_drop_from_queue"):
        content, tid = run._find_and_close_todoist("0neon", "xk20", [])
    assert content == "xk20 (30) [35]"
    assert tid == "TID123"


def test_find_and_close_todoist_no_match_returns_none_none():
    with patch.object(run.todoist, "find_tasks", return_value=[]):
        content, tid = run._find_and_close_todoist("0neon", "xk20", [])
    assert content is None
    assert tid is None


def test_append_completed_body_has_no_unguarded_file_write():
    """Structural guard against reintroducing run.py's old unlocked
    COMPLETED_TODAY.write_text() path: _append_completed's own body must not
    touch COMPLETED_TODAY directly — all writes must route through the shared
    (locked, atomic) mark-completed.py module."""
    source = Path(run.__file__).read_text() if hasattr(run, "__file__") else (_HERE / "run.py").read_text()
    tree = ast.parse((_HERE / "run.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_append_completed")
    body_src = ast.get_source_segment((_HERE / "run.py").read_text(), fn)
    assert "COMPLETED_TODAY" not in body_src, (
        "_append_completed must not write COMPLETED_TODAY directly — "
        "use mc.append_names (locked + atomic) instead"
    )
    assert "mc.append_names" in body_src
