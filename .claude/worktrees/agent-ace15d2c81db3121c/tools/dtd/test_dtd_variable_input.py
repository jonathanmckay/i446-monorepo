#!/usr/bin/env python3
"""Feature (2026-08-11): variable-input tasks in dtd web.

Terminal dtd prompts for a value on completion for tasks like "hiit"
(did-fast.py's VARIABLE_0N) — the typed number gets appended to the name
before routing, so the right 0n column is credited. dtd web had no such
prompt at all: swiping any of these complete silently dropped the value.
build_tasks() now exposes a `variablePrompt` label per task (None for
ordinary tasks) so the frontend can prompt before completing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtd  # noqa: E402


def test_variable_prompt_matches_known_daily_habits():
    assert dtd.variable_prompt("hiit (10) [23]") == "hiit minutes"
    assert dtd.variable_prompt("cpap (15) [15]") == "CPAP quality (1-3)"
    assert dtd.variable_prompt("xk22 (20) [25]") == "xk22 minutes (Ren)"
    assert dtd.variable_prompt("i444 (15) [5]") == "i444 count (0 = none today)"
    assert dtd.variable_prompt("evening hcmc (15) [15]") == "night hcmc minutes"


def test_variable_prompt_is_case_and_annotation_insensitive():
    assert dtd.variable_prompt("HIIT") == "hiit minutes"
    assert dtd.variable_prompt("  hiit  (10)  [23]  ") == "hiit minutes"


def test_variable_prompt_none_for_ordinary_task():
    assert dtd.variable_prompt("call dentist about Theo appointment (10) [5]") is None
    assert dtd.variable_prompt("😈 -1g (15) [15]") is None


def test_variable_prompt_strips_deferred_date_suffix():
    """Regression (2026-08-22): a deferred one-off copy of a daily variable
    habit carries a trailing ' M.D' date suffix (defer-fast / "AoS 7.20"
    convention). The suffix broke the VARIABLE_INPUT lookup, so dtd web
    silently completed it with NO numeric prompt while terminal did-fast (which
    parses the date off first) still prompted. The suffix must be stripped."""
    assert dtd.variable_prompt("xk20 7.26 (30) [35]") == "xk20 minutes (Theo)"
    assert dtd.variable_prompt("hiit 8.20 (10) [23]") == "hiit minutes"
    assert dtd.variable_prompt("新闻 12.3") == "新闻 minutes"


def test_variable_prompt_deferred_suffix_no_false_positive():
    """A deferred copy of a NON-variable task must still return None — stripping
    the date must not turn an ordinary dated task into a variable one."""
    assert dtd.variable_prompt("call dentist 7.26 (10) [5]") is None
    # A bare 'xk20' with no date is unaffected (base case still works).
    assert dtd.variable_prompt("xk20 (30) [35]") == "xk20 minutes (Theo)"


def test_build_tasks_exposes_variable_prompt_field(monkeypatch, tmp_path):
    import datetime as dt
    import json
    cache_f = tmp_path / "task-queue.json"
    cache_f.write_text(json.dumps({
        "updated": dt.datetime.now().isoformat(),
        "today": [
            {"id": "1", "content": "hiit (10) [23]", "labels": ["0neon", "hcbp"],
             "due": dt.date.today().isoformat(), "priority": "p2"},
            {"id": "2", "content": "plain task [5]", "labels": ["i9"],
             "due": dt.date.today().isoformat(), "priority": "p3"},
        ],
        "0neon": [], "1neon": [], "夜neon": [], "关键路径": [],
    }))
    monkeypatch.setattr(dtd, "CACHE", cache_f)
    monkeypatch.setattr(dtd, "DONE_FILE", tmp_path / "completed-today.json")
    monkeypatch.setattr(dtd, "_refresh_cache_if_stale", lambda force=False: None)

    tasks = {t["id"]: t for t in dtd.build_tasks()}
    assert tasks["1"]["variablePrompt"] == "hiit minutes"
    assert tasks["2"]["variablePrompt"] is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
