#!/usr/bin/env python3
"""Regression: task-queue.json writers must be atomic (tmp + rename).

dtd.sh's auto-reload watcher polls this file's mtime every 2s, and its
list-generator does an unguarded json.load() with no retry/except. A writer
that calls `<cache_var>.write_text(...)` directly truncates the file before
the new content lands, so a poll landing in that window reads a
truncated/empty file, the generator crashes on the parse, and dtd's list
goes blank until the next successful poll (up to another 2s) -- the
"flashes then goes blank for a full 2s" bug (2026-08-07).

did-fast.py's own writer (_refresh_task_queue_inner) already does this
correctly: write to a `.tmp` sibling, then `.rename()` onto the real path.
These tests scan the SOURCE of every known task-queue.json writer and fail
if a direct write_text() call on the live cache path reappears.
"""
import ast
from pathlib import Path

MONO = Path.home() / "i446-monorepo"
# (file, name of the Path variable holding the live task-queue.json path)
WRITERS = {
    "did-fast.py": ("TASK_QUEUE_PATH", MONO / "tools" / "did" / "did-fast.py"),
    "refresh-cache.py": ("CACHE", MONO / "tools" / "did" / "refresh-cache.py"),
    "run.py": ("TASK_QUEUE", MONO / "tools" / "did" / "run.py"),
}


def _direct_write_text_targets(tree, live_var):
    """Names that .write_text(...) is called on directly (not a .tmp path)."""
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "write_text"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == live_var):
            offenders.append(node.lineno)
    return offenders


def _has_rename_onto(tree, live_var):
    """True if some call ends with `<something>.rename(<live_var>)`."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "rename"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == live_var):
            return True
    return False


def test_no_direct_write_text_on_live_cache_path():
    """The live cache path variable must never be the .write_text() target
    itself -- only a .tmp path may be, followed by a rename onto it."""
    offenders = []
    for fname, (live_var, path) in WRITERS.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno in _direct_write_text_targets(tree, live_var):
            offenders.append(f"{fname}:{lineno}: {live_var}.write_text(...) is non-atomic")
    assert not offenders, (
        "task-queue.json must be written via tmp-file + rename, not a direct "
        "write_text() on the live path (dtd's 2s-poll watcher can read a "
        "truncated file mid-write and blank the list):\n  " + "\n  ".join(offenders)
    )


def test_every_writer_renames_onto_the_live_path():
    """Each writer that touches the cache must complete the atomic swap with
    an explicit rename() onto the live path variable."""
    missing = []
    for fname, (live_var, path) in WRITERS.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if not _has_rename_onto(tree, live_var):
            missing.append(fname)
    assert not missing, (
        "these writers never rename a tmp file onto the live cache path "
        "(atomic write incomplete): " + ", ".join(missing)
    )


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
