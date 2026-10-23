#!/usr/bin/env python3
"""2026-10-22: /api/add returns the new Todoist id, so the phone's offline
outbox can close a task it added offline by id (its queued done carries a
"$ref:" the outbox swaps for this id) instead of a fuzzy name match."""
import importlib.util
import io
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_addid", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)


def test_add_returns_new_task_id(tmp_path, monkeypatch):
    tok = tmp_path / "token"; tok.write_text("t")
    monkeypatch.setattr(dtd, "TODOIST_TOKEN_FILE", tok)

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(dtd.urllib.request, "urlopen",
                        lambda req, timeout=0: Resp(json.dumps({"id": "6abc", "content": "x"}).encode()))
    r = dtd.app.test_client().post("/api/add", json={"content": "buy milk (5) [2]"})
    assert r.json["ok"] and r.json["id"] == "6abc"
