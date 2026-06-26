"""Regression tests for mark-completed.py dedup + date gate + atomic write."""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

_HERE = Path(__file__).parent
_SPEC = importlib.util.spec_from_file_location("mark_completed", _HERE / "mark-completed.py")
mc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mc)  # type: ignore[union-attr]


def test_dedup_same_name_twice(tmp_path: Path) -> None:
    """Appending the same name twice yields count == 1."""
    f = tmp_path / "completed-today.json"
    mc.append_names(["stats"], path=f)
    mc.append_names(["stats"], path=f)
    data = json.loads(f.read_text())
    assert data["names"].count("stats") == 1
    assert len(data["names"]) == 1


def test_date_gate_resets_on_day_change(tmp_path: Path) -> None:
    """When stored date < today, names reset before append."""
    f = tmp_path / "completed-today.json"
    f.write_text(json.dumps({"date": "1999-01-01", "names": ["old1", "old2"]}))
    today = date.today().isoformat()
    result = mc.append_names(["fresh"], path=f)
    assert result["date"] == today
    assert result["names"] == ["fresh"]
    assert "old1" not in result["names"]


def test_case_insensitive_dedup(tmp_path: Path) -> None:
    """Case and whitespace variants are treated as duplicates."""
    f = tmp_path / "completed-today.json"
    mc.append_names(["HIIT"], path=f)
    mc.append_names(["hiit"], path=f)
    mc.append_names(["  Hiit  "], path=f)
    data = json.loads(f.read_text())
    # Only one entry (first wins). Comparison is normalized.
    assert len(data["names"]) == 1


def test_preexisting_dupes_self_heal(tmp_path: Path) -> None:
    """A no-op append rewrites the file with dupes removed."""
    f = tmp_path / "completed-today.json"
    today = date.today().isoformat()
    f.write_text(json.dumps({
        "date": today,
        "names": ["a", "A", "b", "a", "c"],
    }))
    mc.append_names([], path=f)
    data = json.loads(f.read_text())
    # Order preserved, first occurrence wins.
    assert data["names"] == ["a", "b", "c"]


def test_atomic_write_no_tmp_leftover(tmp_path: Path) -> None:
    """Happy path leaves no .tmp file behind."""
    f = tmp_path / "completed-today.json"
    mc.append_names(["x"], path=f)
    tmp = f.with_suffix(f.suffix + ".tmp")
    assert not tmp.exists()


def test_ids_recorded_for_id_based_hide(tmp_path: Path) -> None:
    """Completed task ids are stored under .ids keyed by normalized name, so dtd
    can hide by id (collision-proof) instead of only by name."""
    f = tmp_path / "completed-today.json"
    mc.append_names(["stats"], path=f, ids={"stats": "AAA"})
    data = json.loads(f.read_text())
    assert data["ids"] == {"stats": "AAA"}


def test_ids_reset_on_day_change(tmp_path: Path) -> None:
    """A stale-date file drops yesterday's ids on the next append."""
    f = tmp_path / "completed-today.json"
    f.write_text(json.dumps({"date": "1999-01-01", "names": ["old"],
                             "ids": {"old": "OLD"}}))
    result = mc.append_names(["fresh"], path=f, ids={"fresh": "NEW"})
    assert result["ids"] == {"fresh": "NEW"}
    assert "old" not in result["ids"]


def test_remove_names_clears_id(tmp_path: Path) -> None:
    """Undo (remove_names) also clears the task's recorded id so it can't keep
    hiding a re-opened task."""
    f = tmp_path / "completed-today.json"
    mc.append_names(["buy plants"], path=f, ids={"buy plants": "PID"})
    mc.remove_names(["buy plants [20]"], path=f)  # undo passes annotated form
    data = json.loads(f.read_text())
    assert data["ids"] == {}
    assert "buy plants" not in data["names"]
