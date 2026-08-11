#!/usr/bin/env python3
"""Feature: quick-add "+" button in dtd web (2026-08-11).

Same (N)/[N]/@tag syntax /todo uses, parsed with plain regex — dtd.py is a
bare Flask process with no LLM access, so unlike /todo's own inference of
missing time/value/domain, an omitted modifier here just stays omitted.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtd  # noqa: E402


def test_parse_add_input_strips_single_tag():
    content, labels = dtd.parse_add_input("buy milk (10) [5] @家")
    assert content == "buy milk (10) [5]"
    assert labels == ["家"]


def test_parse_add_input_no_tags():
    content, labels = dtd.parse_add_input("plain task")
    assert content == "plain task"
    assert labels == []


def test_parse_add_input_multiple_tags_and_extra_whitespace():
    content, labels = dtd.parse_add_input("multi tag @i9 @foo thing")
    assert content == "multi tag thing"
    assert labels == ["i9", "foo"]


def test_api_add_rejects_empty_content(monkeypatch):
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/add", json={"content": "  "})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_api_add_creates_task_due_today_no_project(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        import json as _json
        captured["url"] = req.full_url
        captured["body"] = _json.loads(req.data)
        import io
        class _R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _R(_json.dumps({"id": "new1", "content": captured["body"]["content"]}).encode())

    monkeypatch.setattr(dtd.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(dtd, "TODOIST_TOKEN_FILE", type("P", (), {"read_text": lambda self: "tok"})())

    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/add", json={"content": "call dentist (15) [8] @hcb"})
    d = r.get_json()
    assert d["ok"] is True
    assert captured["body"]["content"] == "call dentist (15) [8]"
    assert captured["body"]["labels"] == ["hcb"]
    assert captured["body"]["due_string"] == "today"
    assert "project_id" not in captured["body"], "must land in Inbox, not a specific project"
    assert captured["body"]["priority"] == 1, "1 == p4 (Todoist's lowest/default), confirmed against a live task"


def test_api_add_surfaces_todoist_error_not_500(monkeypatch):
    def boom(req, timeout=None):
        raise RuntimeError("Todoist 503")
    monkeypatch.setattr(dtd.urllib.request, "urlopen", boom)
    monkeypatch.setattr(dtd, "TODOIST_TOKEN_FILE", type("P", (), {"read_text": lambda self: "tok"})())

    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/add", json={"content": "task"})
    assert r.status_code == 200, "an upstream Todoist failure is a normal {ok:false}, not a 500"
    d = r.get_json()
    assert d["ok"] is False
    assert "503" in d["error"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
