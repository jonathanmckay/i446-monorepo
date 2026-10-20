"""Regression tests for 0g-sync.py — ensures completed Todoist tasks flip
build-order MD lines from `- [ ]` to `- [x]` (rather than removing them, which
would erase the day's accomplishment record from v_logs/archive)."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _load_module():
    # 0g-sync.py has a leading digit and a hyphen in its name — load by spec.
    path = Path(__file__).parent / "0g-sync.py"
    spec = importlib.util.spec_from_file_location("zerog_sync", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["zerog_sync"] = module
    spec.loader.exec_module(module)
    return module


zg = _load_module()


# --- flip_to_checked --------------------------------------------------------

class TestFlipToChecked:
    def test_flips_unchecked_to_checked(self):
        assert zg.flip_to_checked("- [ ] foo") == "- [x] foo"

    def test_preserves_indent(self):
        assert zg.flip_to_checked("    - [ ] block goal") == "    - [x] block goal"

    def test_idempotent_on_checked(self):
        assert zg.flip_to_checked("- [x] done") == "- [x] done"

    def test_ignores_non_task_lines(self):
        assert zg.flip_to_checked("## 0₲") == "## 0₲"

    def test_preserves_annotations(self):
        assert zg.flip_to_checked("- [ ] task {30}") == "- [x] task {30}"


# --- find_neg1_section ------------------------------------------------------

class TestFindNeg1Section:
    def test_finds_section(self):
        lines = [
            "## 0₲",
            "- [ ] foo",
            "## -1₲",
            "- 卯",
            "    - [ ] bar",
            "## next",
        ]
        start, end = zg.find_neg1_section(lines)
        assert start == 2
        assert end == 5

    def test_returns_negative_when_missing(self):
        lines = ["## 0₲", "- [ ] foo"]
        assert zg.find_neg1_section(lines) == (-1, -1)


# --- mark_neg1_completed ----------------------------------------------------

class TestMarkNeg1Completed:
    def test_flips_lines_matching_completed_tasks(self):
        """An unchecked -1₲ line matching a completed Todoist task gets flipped."""
        lines = [
            "## -1₲",
            "- 卯",
            "    - [ ] Improve d357",
            "    - [ ] Keep ibx at 0",
            "    - [ ] Untouched goal",
        ]
        client = MagicMock()
        client.get_completed_tasks.return_value = [
            {"content": "Improve d357 (60)"},
            {"content": "Keep ibx at 0"},
        ]
        flipped = zg.mark_neg1_completed(client, lines, dry_run=True)
        # Lines 2 and 3 should be flipped; line 4 is "Untouched", not in completed list.
        assert sorted(flipped) == [2, 3]

    def test_skips_already_checked_lines(self):
        lines = [
            "## -1₲",
            "    - [x] Already done",
        ]
        client = MagicMock()
        client.get_completed_tasks.return_value = [{"content": "Already done"}]
        assert zg.mark_neg1_completed(client, lines, dry_run=True) == []

    def test_no_op_when_section_missing(self):
        lines = ["## 0₲", "- [ ] foo"]
        client = MagicMock()
        assert zg.mark_neg1_completed(client, lines) == []
        client.get_completed_tasks.assert_not_called()

    def test_skips_blank_unchecked_placeholders(self):
        """The /-1g daily-reset writes empty `- [ ] ` placeholders. Those must
        never match completed tasks (otherwise every empty bullet flips)."""
        lines = [
            "## -1₲",
            "- 卯",
            "    - [ ] ",  # placeholder with trailing space
            "    - [ ]",   # placeholder with no trailing space (won't match CHECKBOX_RE)
        ]
        client = MagicMock()
        client.get_completed_tasks.return_value = [{"content": "Some task"}]
        assert zg.mark_neg1_completed(client, lines, dry_run=True) == []


# --- AST regression: ensure run_sync flips instead of removing --------------

class TestRunSyncBehavior:
    """The original bug: 0g-sync removed completed lines from MD, erasing the
    day's record. The fix flips [ ] to [x] so the 03:59 archive captures it.
    Guard against regression by checking source for the removed pattern."""

    def test_run_sync_does_not_remove_lines(self):
        src = (Path(__file__).parent / "0g-sync.py").read_text()
        # The old buggy logic built a removal set called `lines_to_remove` and
        # did a list comprehension excluding those indices. Any future code
        # that re-introduces a remove-by-index step on the build order MD
        # should be reviewed against this regression.
        assert "lines_to_remove" not in src, (
            "0g-sync.py should flip [ ] to [x] rather than remove lines "
            "(needed so v_logs/archive captures the day's completed tasks)."
        )
        # Affirmative check that we still flip.
        assert "flip_to_checked" in src
