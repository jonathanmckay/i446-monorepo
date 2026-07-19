"""Regression test: the periodic cache refresh (refresh-cache.py) must refresh the
dynamic "today"-bucket labels — -1neon rituals AND #0g/#-1g goals.

Bugs (2026-06-29/30): refresh-cache.py refetched only the 4 neon-label buckets and
PRESERVED the "today" bucket verbatim. -1neon rituals and #0g/#-1g goals live in
"today", so the periodic daemon never surfaced a new block's rituals or newly-set
goals — and the skills' background `--refresh-cache &` doesn't reliably complete,
so the daemon is the dependable path. Fix: refetch DYNAMIC_TODAY_LABELS and splice
into "today", and SIGUSR1 janus so it re-reads.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "refresh-cache.py").read_text()


def _load():
    spec = importlib.util.spec_from_file_location("refresh_cache_m", HERE / "refresh-cache.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_structural_covers_goals_and_nudges_tgtui():
    m = _load()
    assert "-1neon" in m.DYNAMIC_TODAY_LABELS
    assert "#0g" in m.DYNAMIC_TODAY_LABELS, "daemon must refresh #0g goals"
    assert "#-1g" in m.DYNAMIC_TODAY_LABELS, "daemon must refresh #-1g goals"
    main = next(n for n in ast.walk(ast.parse(SRC)) if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.get_source_segment(SRC, main)
    assert 'data["today"] = fresh_dynamic + today_rest' in body, "dynamic labels must be spliced into today"
    assert "_nudge_janus()" in body, "main must SIGUSR1 janus after refresh"


def test_refetched_dynamic_replace_stale_today(tmp_path, monkeypatch):
    m = _load()
    cache = tmp_path / "task-queue.json"
    # Existing cache: a STALE ritual + STALE #0g goal + a real today task to preserve.
    cache.write_text(json.dumps({
        "updated": "2026-06-30T05:00:00",
        "today": [
            {"id": "OLDR", "content": "😈 -1g", "labels": ["-1neon"], "due": "2026-06-30"},
            {"id": "OLDG", "content": "stale goal {10}", "labels": ["#0g", "g245"], "due": "2026-06-30"},
            {"id": "T1", "content": "find car rental (20) [10]", "labels": ["i444"], "due": "2026-06-30"},
        ],
    }))
    monkeypatch.setattr(m, "CACHE", cache)

    def fake_find(labels=None, limit=200):
        if labels == ["-1neon"]:
            return [{"id": "NEWR", "content": "😈 سمش", "labels": ["-1neon"], "due": {"date": "2026-06-30"}}]
        if labels == ["#0g"]:
            return [{"id": "NEWG", "content": "fiction {40}", "labels": ["#0g", "hcm"], "due": {"date": "2026-06-30"}}]
        return []  # #-1g and the 4 neon buckets
    monkeypatch.setattr(m.todoist, "find_tasks", fake_find)
    monkeypatch.setattr(m, "_nudge_janus", lambda: None)

    m.main()
    ids = [t["id"] for t in json.loads(cache.read_text())["today"]]
    assert "NEWR" in ids and "NEWG" in ids, "fresh ritual + goal must be present"
    assert "OLDR" not in ids and "OLDG" not in ids, "stale ritual + goal must be dropped"
    assert "T1" in ids, "non-dynamic today task must be preserved"


def test_empty_fetch_preserves_still_open_dynamic_entries(tmp_path, monkeypatch):
    """Regression (2026-07-19): "-1n tasks disappear ... for like 5 seconds"
    whenever a ritual is completed in dtd.

    find_tasks() has no retry; an empty list it returns is indistinguishable
    from "genuinely nothing open under this label" vs. a transient Todoist
    eventual-consistency hiccup on that label's index — the same lag class
    did-fast.py's own fetch_today already guards against with a retry +
    fallback-to-old. Completing a ritual writes to the SAME -1neon label
    queried here, and this refresh runs both on a launchd timer and
    fire-and-forget after every write, so the race is frequent. Before the
    fix, an empty fetch for -1neon unconditionally wiped the OTHER four
    still-open ritual cards from "today" (today_rest strips every
    dynamic-labeled entry regardless of whether fresh_dynamic actually
    replaced them) until the next successful refresh restored them.
    """
    m = _load()
    cache = tmp_path / "task-queue.json"
    cache.write_text(json.dumps({
        "updated": "2026-07-19T08:00:00",
        "today": [
            {"id": "RIT1", "content": "😈 -1ibx", "labels": ["-1neon"], "due": "2026-07-19"},
            {"id": "RIT2", "content": "😈 -1t", "labels": ["-1neon"], "due": "2026-07-19"},
            {"id": "GOAL1", "content": "block goal {10}", "labels": ["#-1g", "i9"], "due": "2026-07-19"},
            {"id": "T1", "content": "find car rental (20) [10]", "labels": ["i444"], "due": "2026-07-19"},
        ],
    }))
    monkeypatch.setattr(m, "CACHE", cache)

    def fake_find_empty(labels=None, limit=200):
        # Simulate the Todoist label-index lag: every dynamic label transiently
        # returns nothing, even though RIT1/RIT2/GOAL1 are all still open.
        return []
    monkeypatch.setattr(m.todoist, "find_tasks", fake_find_empty)
    monkeypatch.setattr(m, "_nudge_janus", lambda: None)

    m.main()
    ids = [t["id"] for t in json.loads(cache.read_text())["today"]]
    assert "RIT1" in ids and "RIT2" in ids, (
        "an empty -1neon fetch must not wipe still-open ritual cards from the cache")
    assert "GOAL1" in ids, "an empty #-1g fetch must not wipe a still-open block goal"
    assert "T1" in ids, "non-dynamic today task must be preserved"
