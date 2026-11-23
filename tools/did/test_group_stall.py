#!/usr/bin/env python3
"""group_stall.py: one card for task groups that have not moved in 7 days."""
import importlib.util
import sys
from datetime import date
from pathlib import Path

_S = importlib.util.spec_from_file_location("group_stall", Path(__file__).with_name("group_stall.py"))
gs = importlib.util.module_from_spec(_S)
sys.modules["group_stall"] = gs
_S.loader.exec_module(gs)

TODAY = date(2026, 11, 23)


def parent(i, due, updated, labels=("m5x2", "grp"), **kw):
    t = {"id": i, "content": f"outcome {i} (60) [100]", "labels": list(labels),
         "due": {"date": due, "is_recurring": False}, "updated_at": updated + "T10:00:00Z"}
    t.update(kw)
    return t


class Fake:
    def __init__(self, parents, card=None):
        self.parents, self.card, self.posts = parents, card, []

    def find(self, labels=None, content_contains=None, limit=50):
        if labels:
            return self.parents
        return [self.card] if self.card else []

    def request(self, method, path, body=None):
        self.posts.append((path, body))


def test_only_idle_due_group_parents_are_stalled():
    tasks = [parent("old", "2026-11-10", "2026-11-12"),                  # 11 days idle
             parent("touched", "2026-11-10", "2026-11-20"),              # paid 3 days ago
             parent("deferred", "2026-12-01", "2026-11-01"),             # hidden, not in the queue
             parent("justdue", "2026-11-22", "2026-11-01"),              # became due yesterday
             parent("waiting", "2026-11-01", "2026-11-01", labels=("grp", "waiting")),
             parent("plain", "2026-11-01", "2026-11-01", labels=("m5x2",)),
             dict(parent("weekly", "2026-11-01", "2026-11-01"),
                  due={"date": "2026-11-01", "is_recurring": True})]
    assert [(t["id"], d) for t, d in gs.stalled(tasks, TODAY)] == [("old", 11)]


def test_first_run_creates_one_card_listing_the_outcomes():
    f = Fake([parent("a", "2026-11-01", "2026-11-02"), parent("b", "2026-11-10", "2026-11-10")])
    state = {}
    out = gs.run(TODAY, find=f.find, request=f.request, state=state)
    assert out["action"] == "created" and len(f.posts) == 1
    path, body = f.posts[0]
    assert path == "/tasks" and body["content"] == gs.CARD_CONTENT
    assert "," not in body["content"] and ";" not in body["content"]
    assert "outcome a" in body["description"] and "21 days, [100] left" in body["description"]
    assert state == {"a": "2026-11-23", "b": "2026-11-23"}


def test_open_card_is_refreshed_never_duplicated():
    f = Fake([parent("a", "2026-11-01", "2026-11-02")], card={"id": "CARD", "content": gs.CARD_CONTENT})
    out = gs.run(TODAY, find=f.find, request=f.request, state={})
    assert out["action"] == "updated" and f.posts[0][0] == "/tasks/CARD"
    f = Fake([], card={"id": "CARD", "content": gs.CARD_CONTENT})
    gs.run(TODAY, find=f.find, request=f.request, state={})
    assert f.posts[0][1]["description"].startswith("all clear")


def test_closing_the_card_buys_each_listed_outcome_another_week():
    f = Fake([parent("a", "2026-11-01", "2026-11-02"), parent("new", "2026-11-05", "2026-11-05")])
    out = gs.run(TODAY, find=f.find, request=f.request, state={"a": "2026-11-20"})
    assert out["raised"] == ["new"] and "outcome a" not in f.posts[0][1]["description"]
    f = Fake([parent("a", "2026-11-01", "2026-11-02")])
    assert gs.run(TODAY, find=f.find, request=f.request, state={"a": "2026-11-20"})["action"] == "none"
    assert f.posts == []
    assert gs.run(TODAY, find=f.find, request=f.request, state={"a": "2026-11-16"})["action"] == "created"


def test_dry_run_writes_nothing():
    f = Fake([parent("a", "2026-11-01", "2026-11-02")])
    state = {}
    assert gs.run(TODAY, find=f.find, request=f.request, state=state, dry_run=True)["action"] == "created"
    assert f.posts == [] and state == {}
