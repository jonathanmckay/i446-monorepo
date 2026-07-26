#!/usr/bin/env python3
"""Regression (2026-07-26): "when I complete one -1n task, the other -1n
tasks disappear for like 10s".

The completion-triggered refresh follows a burst of Todoist API calls, and
under rate limiting Todoist intermittently returns EMPTY results with a 200
— the failure mode fetch_today already guards. But the label buckets and
the -1neon union had no such guard: one empty label response (combined with
the today|overdue filter's index lag on freshly-created block cards) wrote
a cache with no ritual rows, and dtd's reload dropped them until the next
refresh restored them. Empty fetches with a non-empty old cache must keep
the old entries.
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "did_fast_keepold", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_keepold"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(payload_by_kind):
    """Route by URL: '/tasks/filter' → today filter, '/tasks?label=' → label."""
    def urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        kind = "filter" if "/tasks/filter" in url else "label"
        return _Resp(json.dumps(payload_by_kind[kind]).encode())
    return urlopen


@pytest.fixture()
def df(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "TASK_QUEUE_PATH", tmp_path / "task-queue.json")
    return mod


def _old_cache(df, today):
    rit = {"id": "r2", "content": "😈 -1g (15) [15]", "labels": ["-1neon"],
           "priority": "p1", "due": today, "due_string": "", "recurring": False}
    hab = {"id": "h1", "content": "0t (3) [10]", "labels": ["0neon", "n156"],
           "priority": "p1", "due": today, "due_string": "", "recurring": True}
    plain = {"id": "p1", "content": "plain task [5]", "labels": ["i9"],
             "priority": "p3", "due": today, "due_string": "", "recurring": False}
    df.TASK_QUEUE_PATH.write_text(json.dumps({
        "0neon": [hab], "1neon": [], "夜neon": [], "关键路径": [],
        "today": [plain, rit]}, ensure_ascii=False))
    return rit, hab, plain


def test_empty_label_fetches_keep_old_buckets_and_rituals(df, monkeypatch):
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    rit, hab, plain = _old_cache(df, today)
    # Rate-limited world: every label fetch returns empty; the lagging filter
    # returns only the plain task (no ritual cards).
    monkeypatch.setattr(df.urllib.request, "urlopen", _fake_urlopen({
        "label": {"results": []},
        "filter": {"results": [{"id": "p1", "content": "plain task [5]",
                                "labels": ["i9"], "priority": "p3",
                                "due": {"date": today}}]},
    }))
    cache = df._refresh_task_queue_inner()
    assert cache["0neon"] == [hab], "empty 0neon fetch must keep the old bucket"
    ritual_ids = {t["id"] for t in cache["today"] if "-1neon" in t["labels"]}
    assert ritual_ids == {"r2"}, \
        "empty -1neon fetch must carry the old cache's ritual cards forward"


def test_healthy_fetch_still_replaces_buckets(df, monkeypatch):
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    _old_cache(df, today)
    fresh_lab = {"id": "h9", "content": "fresh habit [5]", "labels": ["0neon"],
                 "priority": "p1", "due": {"date": today}}
    monkeypatch.setattr(df.urllib.request, "urlopen", _fake_urlopen({
        "label": {"results": [fresh_lab]},
        "filter": {"results": [fresh_lab]},
    }))
    cache = df._refresh_task_queue_inner()
    assert [t["id"] for t in cache["0neon"]] == ["h9"], \
        "non-empty fetches must replace, not merge, the old buckets"
