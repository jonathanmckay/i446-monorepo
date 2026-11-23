"""Task groups on dtd web (2026-11-23, lib/dtd_groups.py): one row per group,
the densest due child, with groupMore; children hide with their parent."""
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("dtd_web_groups", Path(__file__).with_name("dtd.py"))
dtd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dtd)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(dtd, "CACHE", tmp_path / "task-queue.json")
    monkeypatch.setattr(dtd, "DONE_FILE", tmp_path / "completed-today.json")
    monkeypatch.setattr(dtd, "SNOOZE_FILE", tmp_path / "snooze.json")
    monkeypatch.setattr(dtd, "DEFERRED_DIR", tmp_path)
    monkeypatch.setattr(dtd, "_refresh_cache_if_stale", lambda force=False: None)
    monkeypatch.setattr(dtd, "_completed_ids", lambda: set())
    monkeypatch.setattr(dtd, "_snoozed_ids", lambda: set())
    monkeypatch.setattr(dtd, "_deferred_habit_ids", lambda: set())
    today = dtd._today().isoformat()

    def write(tasks):
        for t in tasks:
            t.setdefault("due", today)
            t.setdefault("labels", ["m5x2"])
            t.setdefault("priority", 1)
            t.setdefault("recurring", False)
            if t.get("parent_id"):
                t.setdefault("parent_due", today)
                t.setdefault("parent_content", "purchase l912 (240) [1360]")
                t.setdefault("parent_recurring", False)
                t.setdefault("parent_closed", False)
        dtd.CACHE.write_text(json.dumps({"updated": "t", "today": tasks}))
        return {r["id"]: r for r in dtd.build_tasks()}

    return write


def test_group_is_one_row_with_a_count(cache):
    rows = cache([{"id": "P", "content": "purchase l912 (240) [1360]"},
                  {"id": "A", "content": "agree funding (10) [30]", "parent_id": "P"},
                  {"id": "B", "content": "send Ticor reply (5) [40]", "parent_id": "P"},
                  {"id": "X", "content": "unrelated (5) [5]"}])
    assert set(rows) == {"B", "X"}
    assert rows["B"]["groupMore"] == 1 and rows["B"]["groupName"] == "purchase l912"
    assert rows["X"]["groupMore"] == 0 and rows["X"]["groupName"] == ""


def test_children_hide_with_a_later_parent_but_not_an_unknown_one(cache):
    rows = cache([{"id": "A", "content": "x (5) [10]", "parent_id": "P1", "parent_due": "2099-01-01"},
                  {"id": "B", "content": "y (5) [10]", "parent_id": "P2", "parent_closed": True}])
    assert rows == {}
    dtd.CACHE.write_text(json.dumps({"updated": "t", "today": [
        {"id": "C", "content": "z (5) [10]", "parent_id": "P3", "labels": [], "priority": 1,
         "due": dtd._today().isoformat(), "recurring": False}]}))
    assert [r["id"] for r in dtd.build_tasks()] == ["C"]


def test_snoozed_parent_hides_its_children(cache, monkeypatch):
    monkeypatch.setattr(dtd, "_snoozed_ids", lambda: {"P"})
    rows = cache([{"id": "P", "content": "outcome [100]"},
                  {"id": "A", "content": "x (5) [10]", "parent_id": "P"}])
    assert rows == {}


def test_plain_tier_sorts_by_points_per_minute_within_priority(cache):
    cache([{"id": "S", "content": "slow (60) [5]"}, {"id": "F", "content": "fast (10) [20]"}])
    assert [r["id"] for r in dtd.build_tasks()] == ["F", "S"]


def test_outcome_and_owner_flags(cache):
    rows = cache([{"id": "L", "content": "purchase l912 (240) [1360]", "labels": ["m5x2", "grp"]},
                  {"id": "W", "content": "follow up Jesus (10) [8]", "labels": ["m5x2", "waiting"]},
                  {"id": "X", "content": "plain (5) [5]"}])
    assert rows["L"]["groupParent"] and not rows["X"]["groupParent"]
    assert (rows["W"]["owner"], rows["X"]["owner"]) == ("waiting", "")


def test_agent_input_is_folded_under_the_one_jm_can_do(cache):
    rows = cache([{"id": "P", "content": "outcome (60) [100]"},
                  {"id": "A", "content": "agent drafts (5) [90]", "parent_id": "P", "labels": ["m5x2", "agent"]},
                  {"id": "B", "content": "send it (5) [20]", "parent_id": "P"}])
    assert set(rows) == {"B"} and rows["B"]["groupMore"] == 0
    rows = cache([{"id": "P", "content": "outcome (60) [100]"},
                  {"id": "A", "content": "agent drafts (5) [90]", "parent_id": "P", "labels": ["m5x2", "agent"]}])
    assert set(rows) == {"A"} and rows["A"]["owner"] == "agent"
