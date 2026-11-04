"""Tests for dream-audit-todoist.py 'added' attribution.

Bug (v173, 2026-10-30): the brief opened with "DREAM CREATED 5 TODOIST TASKS"
listing 😈 -1l, -1t, -1ibx, -1g and s سمش. Those were added at 04:00 PT by JM's
own ritual emitter (client Python-urllib/3.9), not Dream (todoist-mcp). Fixture
events below are copied from the live activity log for 2026-10-30 11:00Z.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "dream_audit_todoist", Path(__file__).parent / "dream-audit-todoist.py")
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


def _ev(oid, content, client, date="2026-10-30T11:00:10Z"):
    x = {"content": content, "note_count": 0}
    if client is not None:
        x["client"] = client
    return {"event_date": date, "event_type": "added", "extra_data": x,
            "initiator_id": "8054889", "object_id": oid, "object_type": "item"}


EMITTER = [
    _ev("6hrv2JGp3QQ3QmM6", "😈 -1l", "Python-urllib/3.9"),
    _ev("6hrv2JJVvJX6mVw6", "😈 -1t", "Python-urllib/3.9"),
    _ev("6hrv2J9f7g64Q78c", "😈 -1ibx", "Python-urllib/3.9"),
    _ev("6hrv2J6qRRFVFFcc", "😈 -1g", "Python-urllib/3.9"),
    _ev("6hrv2J62qP775CQ6", "😈 s سمش", "Python-urllib/3.9"),
    _ev("6hrvREQ1", "Get 60分 from HIIT in {30}", "python-requests/2.32.5"),
]
MCP = _ev("6hrvMCP1", "tasks below 40 {40}", "todoist-mcp/13.8.0")
NOCLIENT = _ev("6hrvNONE", "mystery", None)


def test_emitter_adds_not_flagged():
    dream, other = audit.attribute_added(EMITTER)
    assert dream == []
    assert len(other) == 6
    assert other[0]["client"] == "Python-urllib/3.9"


def test_mcp_and_unknown_flagged():
    dream, other = audit.attribute_added(EMITTER + [MCP, NOCLIENT])
    assert [d["id"] for d in dream] == ["6hrvMCP1", "6hrvNONE"]
    assert len(other) == 6


def _run_main(tmp_path, monkeypatch, added):
    def fake_fetch(etype, since):
        return added if etype == "added" else []
    monkeypatch.setattr(audit, "fetch_events", fake_fetch)
    monkeypatch.setattr(sys, "argv", ["x", "--run-dir", str(tmp_path),
                                      "--since", "2026-10-30T10:00:00",
                                      "--until", "2026-10-30T13:00:00"])
    (tmp_path / "morning-brief.md").write_text("# brief\n")
    rc = audit.main()
    return rc, (tmp_path / "morning-brief.md").read_text(), \
        json.loads((tmp_path / "revert-mapping.json").read_text())


def test_main_no_banner_for_emitter_only(tmp_path, monkeypatch):
    rc, brief, mapping = _run_main(tmp_path, monkeypatch, EMITTER)
    assert rc == 0
    assert "DREAM CREATED" not in brief
    assert mapping["created"] == [] and len(mapping["added_other"]) == 6


def test_main_banner_lists_only_mcp(tmp_path, monkeypatch):
    rc, brief, mapping = _run_main(tmp_path, monkeypatch, EMITTER + [MCP])
    assert rc == 2
    assert "DREAM CREATED 1 TODOIST TASK" in brief
    assert "6hrvMCP1" in brief and "-1l" not in brief
