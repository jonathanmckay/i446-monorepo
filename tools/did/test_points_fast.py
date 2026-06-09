#!/usr/bin/env python3
"""Regression tests for points-fast.py (dtd ctrl-v: change task points).

Pure logic — no Todoist/network (set_points, resolve_from_cache, patch_cache).
Run: python3 -m pytest test_points_fast.py
"""
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("points_fast", _HERE / "points-fast.py")
pf = importlib.util.module_from_spec(_spec)
sys.modules["points_fast"] = pf
_spec.loader.exec_module(pf)


def test_set_points_replaces_existing():
    assert pf.set_points("call dad (10) [15]", 25) == "call dad (10) [25]"


def test_set_points_appends_when_absent():
    assert pf.set_points("no points here", 8) == "no points here [8]"
    assert pf.set_points("review (30)", 12) == "review (30) [12]"


def test_set_points_only_first_bracket():
    # Only the points bracket should change, not other [..] content
    assert pf.set_points("task [15] note", 3) == "task [3] note"


def _cache():
    return {
        "0neon": [
            {"id": "A1", "content": "call dad (10) [15]"},
            {"id": "B2", "content": "ship feature [40]"},
        ],
        "today": [{"id": "C3", "content": "walk dog"}],
        "refreshed": "ignored-non-list",
    }


def test_resolve_exact_then_prefix():
    c = _cache()
    assert pf.resolve_from_cache(c, "call dad (10) [15]")["id"] == "A1"
    assert pf.resolve_from_cache(c, "ship feature")["id"] == "B2"  # prefix/truncation
    assert pf.resolve_from_cache(c, "nonexistent") is None


def test_patch_cache_updates_by_id():
    c = _cache()
    pf.patch_cache(c, "A1", "call dad (10) [25]")
    assert c["0neon"][0]["content"] == "call dad (10) [25]"
    # other tasks untouched
    assert c["0neon"][1]["content"] == "ship feature [40]"


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
