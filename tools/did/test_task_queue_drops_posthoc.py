"""Regression (2026-11-24): dtd listed "deferred: 1 对身 11.7 (60) [30] →
2026-11-25 (2) [0]" as an open task.

That row is a defer's posthoc audit stub: defer-fast.py creates it (with the
source task's labels plus `posthoc`) and closes it ~400 ms later. A cache
refresh that fetched in that window, or read Todoist's lagging index, wrote it
into task-queue.json as open, and the keep-old fallbacks re-served it. Neither
cache writer may ever write a posthoc-labelled row.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))

import task_queue_filter  # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")
STUB = "deferred: 1 对身 11.7 (60) [30] → 2026-11-25 (2) [0]"


def _load(fname, modname):
    spec = importlib.util.spec_from_file_location(modname, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def _all_ids(cache):
    return {t["id"] for v in cache.values() if isinstance(v, list) for t in v}


def test_drop_posthoc_removes_stub_from_every_bucket_and_warns(capsys):
    cache = {
        "updated": "2026-11-24T07:47:20",
        "1neon": [{"id": "S", "content": STUB, "labels": ["1neon", "hcb", "posthoc"]},
                  {"id": "W", "content": "1 对身 (60) [30]", "labels": ["1neon", "hcb"]}],
        "today": [{"id": "S", "content": STUB, "labels": ["1neon", "hcb", "posthoc"]},
                  {"id": "T", "content": "plain [5]", "labels": ["i9"]}],
    }
    assert set(task_queue_filter.drop_posthoc(cache)) == {"S"}
    assert _all_ids(cache) == {"W", "T"}
    assert cache["updated"] == "2026-11-24T07:47:20"
    assert "S" in capsys.readouterr().err, "a still-open stub must be named, not hidden silently"


def test_refresh_cache_never_writes_posthoc_stub(tmp_path, monkeypatch):
    m = _load("refresh-cache.py", "refresh_cache_posthoc")
    cache = tmp_path / "task-queue.json"
    stub = {"id": "S", "content": STUB, "labels": ["1neon", "hcb", "posthoc"], "due": TODAY}
    # Old cache already holds the stub (keep-old path) ...
    cache.write_text(json.dumps({
        "updated": "2026-11-24T07:47:20",
        "1neon": [stub],
        "today": [stub, {"id": "T", "content": "plain [5]", "labels": ["i9"], "due": TODAY}],
    }, ensure_ascii=False))
    monkeypatch.setattr(m, "CACHE", cache)

    # ... and the lagging label index still returns it as open.
    def fake_find(labels=None, limit=200):
        if labels == ["1neon"]:
            return [{"id": "S", "content": STUB, "labels": stub["labels"], "due": {"date": TODAY}},
                    {"id": "W", "content": "1 对身 (60) [30]", "labels": ["1neon", "hcb"],
                     "due": {"date": TODAY}}]
        return []
    monkeypatch.setattr(m.todoist, "find_tasks", fake_find)
    monkeypatch.setattr(m, "_nudge_janus", lambda: None)

    m.main()
    ids = _all_ids(json.loads(cache.read_text()))
    assert "S" not in ids, "posthoc stub must not be written to the task queue"
    assert {"W", "T"} <= ids, "real tasks must survive the filter"


def test_refresh_cache_drops_stub_carried_only_by_old_cache(tmp_path, monkeypatch):
    m = _load("refresh-cache.py", "refresh_cache_posthoc_old")
    cache = tmp_path / "task-queue.json"
    stub = {"id": "S", "content": STUB, "labels": ["1neon", "hcb", "posthoc"], "due": TODAY}
    cache.write_text(json.dumps({"updated": "2026-11-24T07:47:20",
                                 "1neon": [stub], "today": [stub]}, ensure_ascii=False))
    monkeypatch.setattr(m, "CACHE", cache)
    monkeypatch.setattr(m.todoist, "find_tasks", lambda labels=None, limit=200: [])
    monkeypatch.setattr(m, "_nudge_janus", lambda: None)

    m.main()
    assert "S" not in _all_ids(json.loads(cache.read_text())), \
        "an empty fetch must not let keep-old re-serve the stub"


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_did_fast_refresh_never_writes_posthoc_stub(tmp_path, monkeypatch):
    df = _load("did-fast.py", "did_fast_posthoc")
    monkeypatch.setattr(df, "TASK_QUEUE_PATH", tmp_path / "task-queue.json")
    monkeypatch.setattr(df.mc, "COMPLETED", tmp_path / "completed-today.json")
    stub = {"id": "S", "content": STUB, "labels": ["1neon", "hcb", "posthoc"],
            "priority": "p4", "due": {"date": TODAY}}
    real = {"id": "W", "content": "1 对身 (60) [30]", "labels": ["1neon", "hcb"],
            "priority": "p3", "due": {"date": TODAY}}

    def urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/tasks/filter" in url:
            return _Resp(json.dumps({"results": [stub, real]}).encode())
        if "label=1neon" in url:
            return _Resp(json.dumps({"results": [stub, real]}).encode())
        if "label=" in url:
            return _Resp(json.dumps({"results": []}).encode())
        return _Resp(json.dumps({"checked": False}).encode())
    monkeypatch.setattr(df.urllib.request, "urlopen", urlopen)

    cache = df._refresh_task_queue_inner()
    assert "S" not in _all_ids(cache), "posthoc stub must not be written to the task queue"
    assert "W" in _all_ids(cache)
    assert "S" not in _all_ids(json.loads(df.TASK_QUEUE_PATH.read_text()))
