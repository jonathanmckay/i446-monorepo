"""Regression tests for dtd.build_tasks hide logic.

Bug (2026-07-03): after a 2h block boundary, the daemon deletes+recreates the
-1neon block rituals (سمش / -1g / -1ibx) with fresh ids, but dtd hid them by
NAME. '😈 -1g' lived in completed-today.names as a name-only entry (a goal-set
recorded it with no id), so it suppressed the newly-created -1g card for the
rest of the day — '-1n tasks did not reappear in dtd'. Fix: hide by id ONLY.
"""
import datetime as _dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtd  # noqa: E402

TODAY = _dt.date.today().isoformat()


def _setup(monkeypatch, tmp_path, cache, done):
    cache_f = tmp_path / "task-queue.json"
    done_f = tmp_path / "completed-today.json"
    cache_f.write_text(json.dumps(cache))
    done_f.write_text(json.dumps(done))
    monkeypatch.setattr(dtd, "CACHE", cache_f)
    monkeypatch.setattr(dtd, "DONE_FILE", done_f)
    # never hit the network / did-fast during tests
    monkeypatch.setattr(dtd, "_refresh_cache_if_stale", lambda force=False: None)


def _ritual(tid, tag):
    return {"id": tid, "content": f"😈 {tag}", "labels": ["-1neon"],
            "due": TODAY, "priority": 1}


def test_recreated_block_ritual_reappears_despite_name_only_completion(monkeypatch, tmp_path):
    # -1g is open with a FRESH id; سمش & -1ibx were completed this block (id-backed).
    cache = {
        "updated": _dt.datetime.now().isoformat(),
        "today": [_ritual("NEW1G", "-1g"),
                  _ritual("SAMSU", "سمش"),
                  _ritual("IBX", "-1ibx")],
        "0neon": [], "1neon": [], "夜neon": [], "关键路径": [],
    }
    done = {
        "date": TODAY,
        # '😈 -1g' is a NAME-ONLY completion (phantom goal-set, no id) — must NOT hide NEW1G
        "names": ["😈 سمش", "😈 -1g", "😈 -1ibx"],
        "ids": {"😈 سمش": "SAMSU", "😈 -1ibx": "IBX"},
    }
    _setup(monkeypatch, tmp_path, cache, done)

    ids = {t["id"] for t in dtd.build_tasks()}
    assert "NEW1G" in ids          # the open -1g must reappear
    assert "SAMSU" not in ids      # id-backed completion stays hidden
    assert "IBX" not in ids


def test_stale_completed_file_from_prior_day_hides_nothing(monkeypatch, tmp_path):
    cache = {
        "updated": _dt.datetime.now().isoformat(),
        "today": [_ritual("NEW1G", "-1g")],
        "0neon": [], "1neon": [], "夜neon": [], "关键路径": [],
    }
    done = {"date": "2000-01-01", "names": ["😈 -1g"], "ids": {"😈 -1g": "NEW1G"}}
    _setup(monkeypatch, tmp_path, cache, done)
    ids = {t["id"] for t in dtd.build_tasks()}
    assert "NEW1G" in ids  # yesterday's completions never gate today's list


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
