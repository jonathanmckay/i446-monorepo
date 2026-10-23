#!/usr/bin/env python3
"""2026-10-22: one session can hold several claims (`claim --also`, for work
fanned out to subagents), and `done` marks a claim finished-by-the-agent so
dtd draws ✅ on the left until JM completes it."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("ac_done", Path(__file__).parent / "agent_claims.py")
ac = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ac)


def test_also_keeps_earlier_claims(tmp_path):
    ac.claim({"id": "a", "content": "A"}, "S", tmp_path)
    ac.claim({"id": "b", "content": "B"}, "S", tmp_path, also=True)
    assert ac.held("S", tmp_path) == ["a", "b"]
    ac.claim({"id": "c", "content": "C"}, "S", tmp_path)      # plain claim replaces
    assert ac.held("S", tmp_path) == ["c"]
    assert ac.load(tmp_path)["a"]["state"] == "released"


def test_done_marks_and_leaves_index(tmp_path):
    ac.claim({"id": "a", "content": "A"}, "S", tmp_path)
    ac.claim({"id": "b", "content": "B"}, "S", tmp_path, also=True)
    assert ac.mark_done("S", ["a"], tmp_path) == ["a"]
    claims = ac.load(tmp_path)
    assert claims["a"]["done"] and not claims["a"]["working"] and claims["a"]["pane"] is None
    assert not claims["b"]["done"]
    assert ac.held("S", tmp_path) == ["b"]
    ac.mark_done("S", None, tmp_path)                          # default: everything held
    assert ac.load(tmp_path)["b"]["done"] and ac.held("S", tmp_path) == []


def test_release_all_held(tmp_path):
    ac.claim({"id": "a", "content": "A"}, "S", tmp_path)
    ac.claim({"id": "b", "content": "B"}, "S", tmp_path, also=True)
    assert ac.release("S", tmp_path) == "a"
    assert {r["state"] for r in ac.load(tmp_path).values()} == {"released"}
