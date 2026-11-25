"""MCP-created entries carry the `😈` tag (JM 2026-11-24: "tag all agent
written time entries"; jm-ai-risks risk 3).

Must hold:
- toggl_create_entry and toggl_batch tag every entry they create, keeping any
  other tag (kid sub-project tags drive kid-time credit) and never doubling it.
- dictated=True skips the tag: /did passes it for a time range JM stated.
- toggl_start stays untagged: a live timer is measured, not inferred.
- the server labels its source-log rows `mcp-toggl`, not `inline`.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from test_toggl_cli_today import _load_server_module  # noqa: E402

TAG = "😈"


def _capture(server, monkeypatch):
    calls = []

    def fake_create(description, start_iso, stop_iso, duration_sec, project_id=None, tags=None):
        calls.append({"desc": description, "tags": tags})
        return {"id": len(calls), "description": description, "start": start_iso, "stop": stop_iso,
                "duration": duration_sec, "project_id": project_id, "tags": tags or []}

    monkeypatch.setattr(server.toggl_api, "create_entry", fake_create)
    return calls


def test_created_entries_get_the_agent_tag_alongside_others(monkeypatch):
    server = _load_server_module()
    calls = _capture(server, monkeypatch)
    server.toggl_create_entry("gap fill", "10:00", "10:10", project="i447")
    server.toggl_create_entry("xk26", "10:10", "10:20", project="xk87", tags=["xk26"])
    server.toggl_create_entry("again", "10:20", "10:30", project="i447", tags=[TAG])
    assert calls[0]["tags"] == [TAG]
    assert calls[1]["tags"] == ["xk26", TAG]
    assert calls[2]["tags"] == [TAG]


def test_batch_tags_every_entry_and_keeps_the_subproject_tag(monkeypatch):
    server = _load_server_module()
    calls = _capture(server, monkeypatch)
    out = server.toggl_batch("0900-0910 a @i447, 0910-0920 b @xk26", date="2026-11-24")
    assert "2 created" in out
    assert calls[0]["tags"] == [TAG]
    assert calls[1]["tags"] == ["xk26", TAG]


def test_dictated_entries_are_not_tagged(monkeypatch):
    server = _load_server_module()
    calls = _capture(server, monkeypatch)
    server.toggl_create_entry("bball", "10:00", "11:00", project="hcb", dictated=True)
    server.toggl_batch("0900-0910 a @i447", date="2026-11-24", dictated=True)
    assert calls[0]["tags"] is None and calls[1]["tags"] is None


def test_live_timer_is_not_tagged_and_source_log_names_the_server(monkeypatch):
    server = _load_server_module()
    started = {}
    monkeypatch.setattr(server.toggl_api, "start_timer",
                        lambda description, project_id=None, tags=None, start_time=None:
                        started.update(tags=tags) or {"id": 1, "description": description, "start": "2026-11-24T10:00:00+00:00",
                                                      "duration": -1, "tags": tags or []})
    server.toggl_start("deep work", project="i447")
    assert started["tags"] is None
    assert os.environ.get("JMSOURCE_VIA") == "mcp-toggl"
