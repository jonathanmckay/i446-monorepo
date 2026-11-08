#!/usr/bin/env python3
"""v180's brief opened with "⛔ DREAM CREATED 5 TODOIST TASKS" for the five
04:00 PT block-ritual cards scripts/build-order-daemon.py adds. These are the
real activity-log events (GET /api/v1/activities?object_type=item&
event_type=added, 2026-11-06T11:55Z..12:05Z), verbatim. The launcher then ran
wip's audit, which had no attribution at all."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "audit_v180", Path(__file__).with_name("dream-audit-todoist.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

PROJECT = "6Crfmq5PfP5VQ286"
V180_RITUALS = [
    {"object_id": oid, "event_date": ts, "event_type": "added", "object_type": "item",
     "parent_project_id": PROJECT,
     "extra_data": {"client": "Python-urllib/3.9", "content": content, "note_count": 0}}
    for oid, ts, content in [
        ("6hwVm3WCrvgF8mP6", "2026-11-06T12:00:15.876554Z", "😈 -1l"),
        ("6hwVm3c3wjxrqhfc", "2026-11-06T12:00:15.301327Z", "😈 -1t"),
        ("6hwVm3QFf3c35Fwc", "2026-11-06T12:00:14.723902Z", "😈 -1ibx"),
        ("6hwVm3MGXjXH2fGc", "2026-11-06T12:00:13.865122Z", "😈 -1g"),
        ("6hwVm3Fw2MCGFJp6", "2026-11-06T12:00:12.774205Z", "😈 s سمش"),
    ]
]
IDS = {e["object_id"] for e in V180_RITUALS}
DREAM_ADD = {"object_id": "dreamX", "event_date": "2026-11-06T12:10:00Z",
             "extra_data": {"client": "todoist-mcp/13.8.0", "content": "Call Nate re GDM"}}


def _run(added):
    events = {"added": added, "updated": [], "deleted": [], "completed": []}
    audit.fetch_events = lambda etype, since: events[etype]
    d = Path(tempfile.mkdtemp())
    # v180's claude window: started 03:3x PST (11:3x UTC); 04:00 PST block start is inside it.
    sys.argv = ["x", "--run-dir", str(d), "--since", "2026-11-06T11:31:00",
                "--until", "2026-11-06T14:30:00"]
    return audit.main(), d


def test_classify_v180_rituals_as_emitter():
    split = audit.classify_added(V180_RITUALS)
    assert split["dream"] == [] and split["other"] == []
    assert {r["id"] for r in split["emitter"]} == IDS


def test_v180_rituals_raise_no_banner():
    rc, d = _run(V180_RITUALS)
    assert rc == 0
    assert not (d / "morning-brief.md").exists()
    m = json.loads((d / "revert-mapping.json").read_text())
    assert m["created"] == []
    assert {r["id"] for r in m["emitter_added"]} == IDS
    assert m["counts"]["added"] == 5


def test_ritual_content_from_mcp_client_is_still_dreams():
    # A 😈 task Dream itself writes through MCP is a violation, not an emitter card.
    fake = dict(V180_RITUALS[0], object_id="mcp😈",
                extra_data={"client": "todoist-mcp/13.8.0", "content": "😈 -1l"})
    assert [r["id"] for r in audit.classify_added([fake])["dream"]] == ["mcp😈"]


def test_real_dream_add_alongside_rituals_is_flagged_alone():
    rc, d = _run(V180_RITUALS + [DREAM_ADD])
    brief = (d / "morning-brief.md").read_text()
    assert rc == 2
    assert "DREAM CREATED 1 TODOIST TASK" in brief and "dreamX" in brief
    assert not any(i in brief for i in IDS)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("4 passed")
