"""Regression test: the periodic cache refresh (refresh-cache.py) must refresh
the -1neon ritual cards.

Bug (2026-06-29): refresh-cache.py only refetched the 4 neon-label buckets and
PRESERVED the old "today" bucket verbatim. The -1neon block-ritual cards live in
"today", so the periodic daemon never surfaced a new block's rituals — dtd and
tg-tui showed a stale ritual set (or none) until a full did-fast --refresh-cache.
Fix: refetch -1neon and splice it into "today", and SIGUSR1 tg-tui so it re-reads.
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


def test_structural_splices_rituals_and_nudges_tgtui():
    tree = ast.parse(SRC)
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.get_source_segment(SRC, main)
    assert "RITUAL_LABEL" in body, "main must refetch the -1neon ritual label"
    assert 'data["today"] = fresh_rituals + today_rest' in body, "rituals must be spliced into today"
    assert "_nudge_tg_tui()" in body, "main must SIGUSR1 tg-tui after refresh"


def test_refetched_rituals_replace_stale_today(tmp_path, monkeypatch):
    m = _load()
    cache = tmp_path / "task-queue.json"
    # Existing cache: a STALE ritual + a real today task to preserve.
    cache.write_text(json.dumps({
        "updated": "2026-06-29T11:29:00",
        "today": [
            {"id": "OLD", "content": "😈 -1g (15) [15]", "labels": ["-1neon"], "due": "2026-06-29"},
            {"id": "T1", "content": "find car rental (20) [10]", "labels": ["i444"], "due": "2026-06-29"},
        ],
    }))
    monkeypatch.setattr(m, "CACHE", cache)

    def fake_find(labels=None, limit=200):
        if labels == [m.RITUAL_LABEL]:
            return [{"id": "NEW", "content": "😈 سمش", "labels": ["-1neon"], "due": {"date": "2026-06-29"}}]
        return []  # the 4 neon buckets
    monkeypatch.setattr(m.todoist, "find_tasks", fake_find)
    monkeypatch.setattr(m, "_nudge_tg_tui", lambda: None)  # no signal in test

    m.main()
    today = json.loads(cache.read_text())["today"]
    ids = [t["id"] for t in today]
    assert "NEW" in ids, "fresh current-block ritual must be present"
    assert "OLD" not in ids, "stale -1neon entry must be dropped"
    assert "T1" in ids, "non-ritual today task must be preserved"
