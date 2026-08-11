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
    status = 200  # _todoist_request reads resp.status; plain BytesIO has none

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


def _fake_urlopen_with_individual(payload_by_kind, individual_by_id):
    """Like _fake_urlopen, but also answers individual `/tasks/<id>` GETs
    (no '/filter', no '?label=') from `individual_by_id` — used to fake the
    live re-GET verification pass's ground truth for a specific card."""
    def urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/tasks/filter" in url:
            return _Resp(json.dumps(payload_by_kind["filter"]).encode())
        if "label=" in url:
            return _Resp(json.dumps(payload_by_kind["label"]).encode())
        tid = url.rstrip("/").rsplit("/", 1)[-1]
        return _Resp(json.dumps(individual_by_id.get(tid, {"checked": False})).encode())
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


def test_closed_ritual_not_resurrected_by_stale_today_fallback(df, monkeypatch, tmp_path):
    """2026-08-11 bug: "-1n tasks rendering in dtd web even though I've done
    them". A flaky/empty fetch_today() (both the initial call and its retry)
    fell back to the ENTIRE old 'today' list with zero filtering — unlike the
    -1neon union's own carry-forward, which re-verifies each candidate's live
    status before carrying it. A ritual already closed via run_ritual (which
    records into completed-today.json) was silently resurrected by this
    fallback, and stayed resurrected on every subsequent refresh since each
    refresh's carry-forward becomes the next refresh's old_cache.
    """
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    rit, hab, plain = _old_cache(df, today)
    completed_path = tmp_path / "completed-today.json"
    completed_path.write_text(json.dumps(
        {"date": today, "names": ["-1g"], "ids": {"-1g": "r2"}}))
    monkeypatch.setattr(df.mc, "COMPLETED", completed_path)
    # Both fetch_today calls (initial + retry) return empty — forces the
    # old_today fallback path this test targets.
    monkeypatch.setattr(df.urllib.request, "urlopen", _fake_urlopen({
        "label": {"results": []},
        "filter": {"results": []},
    }))
    monkeypatch.setattr("time.sleep", lambda *_: None)  # skip the retry's real delay
    cache = df._refresh_task_queue_inner()
    today_ids = {t["id"] for t in cache["today"]}
    assert "r2" not in today_ids, \
        "an already-closed ritual must not be resurrected by the stale-today fallback"
    assert "p1" in today_ids, "a non-completed task must still be carried forward"


def test_stale_healthy_fetch_ritual_verified_and_dropped_if_actually_closed(df, monkeypatch):
    """2026-08-11 bug: "dtd web shows -1n tasks when they are already
    finished in the main dtd". Reproduced live: a ritual closed via
    run_ritual a full HOUR earlier still came back from a HEALTHY
    (non-empty) today|overdue filter fetch as open — worse than the
    "minutes" of index lag the carry-forward guards were built to tolerate,
    and this card was never a carry-forward candidate in the first place
    (it came straight from a non-empty fetch, so none of those guards ever
    ran). completed-today.json couldn't have caught it either: it only held
    ids from an EARLIER block's rituals — -1neon cards are deleted and
    recreated every 2h, so a later block's completions are a different id
    generation entirely. Fix: every -1neon card is individually re-verified
    against a live GET right before the cache write, regardless of which
    fetch it came from."""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    stale_open_ritual = {"id": "r9", "content": "😈 -1g", "labels": ["-1neon"],
                         "priority": "p1", "due": {"date": today}}
    plain = {"id": "p1", "content": "plain task [5]", "labels": ["i9"],
             "priority": "p3", "due": {"date": today}}
    monkeypatch.setattr(df.urllib.request, "urlopen", _fake_urlopen_with_individual(
        {"filter": {"results": [plain, stale_open_ritual]},
         "label": {"results": [stale_open_ritual]}},
        {"r9": {"checked": True}},  # ground truth: actually closed
    ))
    cache = df._refresh_task_queue_inner()
    today_ids = {t["id"] for t in cache["today"]}
    assert "r9" not in today_ids, \
        "a -1neon card confirmed closed via live re-GET must be dropped, even from a healthy fetch"
    assert "p1" in today_ids, "a non-ritual task must be unaffected by the verification pass"


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
