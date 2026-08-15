"""Regression: every daily Todoist habit defined in config/tasks.json must
have a matching entry in config/daily-todoist-manifest.json, or
validate-daily-habits.py's --fix safety net can never detect or recreate it
if its live recurring Todoist task ever vanishes (sync hiccup, accidental
delete, a completion that didn't roll forward — see the module docstring).

Bug (found 2026-08-15, reported as "I don't see xk26 for today"): the
manifest was missing 16 of the 33 `0neon`-labeled habits in tasks.json,
including all three kid-time trackers (xk20 Theo, xk22 Ren, xk26 Rori) —
they happened to still have live Todoist cards at the time, but nothing
would have caught or repaired it had one actually gone missing. Fixed by
adding entries for xk20/xk22/xk26 built from their real live Todoist task
content (content/labels/priority/project_id), matching the existing
manifest entries' shape.

This test only covers the three xk trackers directly implicated in the bug
report, not the full 16-habit gap (the other 13 are a separate, still-open
cleanup — see the /bug investigation) — asserting the full set would require
verifying each one's real live Todoist content the same way, which wasn't
done here.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = json.loads((ROOT / "config" / "tasks.json").read_text())
MANIFEST = json.loads((ROOT / "config" / "daily-todoist-manifest.json").read_text())


def test_kid_time_habits_are_manifest_covered():
    """xk20/xk22/xk26 must all be in the manifest — the exact gap that let
    "xk26 missing today" go undetected/unrepaired."""
    for key in ("xk20", "xk22", "xk26"):
        assert key in MANIFEST["habits"], (
            f"{key} is a daily 0neon habit in tasks.json but missing from "
            "daily-todoist-manifest.json — the --fix safety net cannot "
            "detect or recreate it if its Todoist card ever vanishes")


def test_kid_time_manifest_entries_match_tasks_json_domain():
    """Each xk entry's manifest labels must include its own habit code and
    the shared xk87 family domain, matching tasks.json's definition."""
    for key in ("xk20", "xk22", "xk26"):
        habit = TASKS["habits"][key]
        assert habit["domain"] == "xk87"
        entry = MANIFEST["habits"][key]
        assert entry["match"] == key
        assert key in entry["labels"]
        assert "xk87" in entry["labels"]
        assert entry["due_string"] == "every day"


def test_kid_time_habits_are_0neon_labeled_in_tasks_json():
    """Sanity check on the premise: xk20/xk22/xk26 really are meant to be
    daily-recreatable Todoist habits, not on-demand-only (compare the
    now-confirmed-nonexistent 'xk86' from the same bug report, which has no
    tasks.json entry at all)."""
    for key in ("xk20", "xk22", "xk26"):
        assert TASKS["habits"][key]["todoist_label"] == "0neon"
    assert "xk86" not in TASKS["habits"]


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
