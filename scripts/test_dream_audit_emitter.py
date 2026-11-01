#!/usr/bin/env python3
"""Neon-emitter 😈 -1neon tasks must not trip the creation banner (pattern 1161)."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "audit", Path(__file__).with_name("dream-audit-todoist.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

EVENTS = {
    "added": [
        {"object_id": "e1", "event_date": "2026-10-22T11:00:12Z",
         "extra_data": {"client": "Python-urllib/3.9", "content": "😈 -1l"}},
        {"object_id": "d1", "event_date": "2026-10-22T11:30:00Z",
         "extra_data": {"client": "todoist-mcp/13.6.0", "content": "😈 dream-made task"}},
    ],
    "updated": [], "deleted": [], "completed": [],
}


def run(events):
    audit.fetch_events = lambda etype, since: events[etype]
    d = Path(tempfile.mkdtemp())
    sys.argv = ["x", "--run-dir", str(d), "--since", "2026-10-22T10:00:00",
                "--until", "2026-10-22T13:00:00"]
    rc = audit.main()
    return rc, d


def test_emitter_only_no_banner():
    rc, d = run({**EVENTS, "added": EVENTS["added"][:1]})
    assert rc == 0 and not (d / "morning-brief.md").exists()
    m = json.loads((d / "revert-mapping.json").read_text())
    assert m["created"] == [] and m["emitter_added"][0]["id"] == "e1"


def test_real_creation_still_flagged():
    rc, d = run(EVENTS)
    brief = (d / "morning-brief.md").read_text()
    assert rc == 2 and "d1" in brief and "e1" not in brief


if __name__ == "__main__":
    test_emitter_only_no_banner()
    test_real_creation_still_flagged()
    print("2 passed")
