"""Regression: deferring a daily habit by 1 day must hide it for the REST of
today in dtd — one defer, not two (bug 2026-07-21: xk20/xk22/xk26 needed a
second defer because (a) the non-recurring deferred copy landed inside dtd's
due<=tomorrow drift-guard bound with the cache's `recurring` flag stripped,
and (b) the advanced recurring parent is cache-identical to the 2026-06-27
over-advance drift case, so intent must come from the per-day
habits-deferred-<date>.ids marker written by defer-fast).
"""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── defer-fast: marker writer ───────────────────────────────────────────────

def test_marker_written_for_daily_habit(monkeypatch, tmp_path):
    df = _load("defer_fast_marker", "defer-fast.py")
    marker = tmp_path / "habits-deferred.ids"
    monkeypatch.setattr(df, "deferred_marker_path", lambda when=None: marker)
    df.mark_habit_deferred("TASK123", ["0neon", "xk26", "xk87"])
    df.mark_habit_deferred("TASK456", ["夜neon"])
    assert marker.read_text().splitlines() == ["TASK123", "TASK456"]


def test_marker_skipped_for_non_habit(monkeypatch, tmp_path):
    df = _load("defer_fast_marker2", "defer-fast.py")
    marker = tmp_path / "habits-deferred.ids"
    monkeypatch.setattr(df, "deferred_marker_path", lambda when=None: marker)
    df.mark_habit_deferred("TASK789", ["xk87"])  # ordinary task, not a daily habit
    assert not marker.exists()


def test_marker_path_is_day_keyed():
    df = _load("defer_fast_marker3", "defer-fast.py")
    p = df.deferred_marker_path(dt.date(2026, 7, 21))
    assert p.name == "habits-deferred-2026-07-21.ids"


def test_handle_recurring_marks_the_parent():
    """Source-level: the parent advance in handle_recurring must be followed by
    mark_habit_deferred — covering BOTH copy and skip modes (both share step 2)."""
    src = (HERE / "defer-fast.py").read_text()
    body = src[src.index("def handle_recurring"):src.index("def main")]
    assert "mark_habit_deferred(task_id, labels)" in body


# ── refresh-cache: recurring survives shaping ───────────────────────────────

def test_refresh_cache_shape_keeps_recurring():
    rc = _load("refresh_cache_shape", "refresh-cache.py")
    raw = {"id": "T1", "content": "xk26 (10) [10]", "labels": ["0neon"],
           "due": {"date": "2026-07-22", "is_recurring": True}}
    assert rc._shape(raw)["recurring"] is True
    copy = {"id": "T2", "content": "xk26 7.21 (10) [10]", "labels": ["0neon"],
            "due": {"date": "2026-07-22", "is_recurring": False}}
    assert rc._shape(copy)["recurring"] is False
    # Already-shaped entries (the empty-fetch fallback re-splices cache rows).
    assert rc._shape({"id": "T3", "due": "2026-07-22", "recurring": True})["recurring"] is True


# ── dtd list builder: marker + recurring filters present ────────────────────

def test_dtd_list_builder_hides_deferred_ids_unconditionally():
    src = (HERE / "dtd.sh").read_text()
    assert "habits-deferred-" in src
    assert "not in _deferred_ids" in src
    block = src[src.index("_deferred_ids = set()"):src.index("oneneon =")]
    # Unconditional id hide, then the recurring/due drift-guard condition.
    assert "t.get('id') not in _deferred_ids" in block
    assert "t.get('recurring', True) or t['due'] <= today" in block
    # Marker path must come from the current date, not the session-start arg.
    assert "_dt.date.today()" in block


def test_list_builder_heredoc_contains_no_double_quotes():
    """The list builder's python lives inside a zsh double-quoted
    `python3 -c "..."` string — a literal double quote anywhere in it splits
    the -c argument and the whole list dies with
    `int() base 10: '2026-07-21'` (broke live 2026-07-21 via a comment
    containing "xk22 7.21"). Single quotes only, per the in-file NB."""
    src = (HERE / "dtd.sh").read_text()
    start = src.index("cat > \"$DTD_LIST\" << 'LISTEOF'")
    end = src.index("LISTEOF", start + 40)
    block = src[start:end]
    py = block[block.index('python3 -c "') + len('python3 -c "'):block.rindex('" "$1"')]
    bad = [l for l in py.splitlines() if '"' in l]
    assert not bad, f"double quotes inside the -c string: {bad[:3]}"


# ── undo-fast: ctrl-z unhides ───────────────────────────────────────────────

def test_undo_unmarks_only_that_id(monkeypatch, tmp_path):
    uf = _load("undo_fast_unmark", "undo-fast.py")
    p = tmp_path / f"habits-deferred-{dt.date.today().isoformat()}.ids"
    p.write_text("AAA\nBBB\n")
    monkeypatch.setattr(uf.os.path, "expanduser", lambda _: str(p))
    uf._unmark_habit_deferred("AAA")
    assert p.read_text() == "BBB\n"


def test_undo_defer_branch_calls_unmark():
    src = (HERE / "undo-fast.py").read_text()
    branch = src[src.index('elif rtype == "defer"'):src.index('elif rtype == "split"')]
    assert "_unmark_habit_deferred(tid)" in branch


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
