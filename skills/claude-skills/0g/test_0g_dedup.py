"""Regression tests for 0g skill deduplication (SKILL.md)."""
from pathlib import Path

SKILL_MD = Path(__file__).parent / "SKILL.md"


def test_todoist_add_step_requires_dedup_for_with_args():
    """
    Bug: /0g created duplicate Todoist tasks when run twice with the same goals,
    or when run with args then without args. e.g. two 'get through qz12' tasks.

    Fix: Step 3 (with args) must fetch existing tasks and skip duplicates.
    """
    text = SKILL_MD.read_text()
    # Find the "with arguments" add-to-todoist section
    with_args_section = text[text.index("With arguments"):text.index("Without arguments")]
    assert "dedup" in with_args_section.lower(), (
        "Step 3 (with args) must include dedup check before creating Todoist tasks"
    )
    assert "find-tasks" in with_args_section, (
        "Step 3 (with args) must use find-tasks to check for existing tasks"
    )
    assert "skip" in with_args_section.lower(), (
        "Step 3 (with args) must skip creation of duplicate tasks"
    )


def test_todoist_add_step_requires_dedup_for_sync():
    """
    Bug: /0g (no args, sync mode) created duplicate Todoist tasks when goals
    already existed from a previous /0g run.

    Fix: Step 2 (without args) must fetch existing tasks and skip duplicates.
    """
    text = SKILL_MD.read_text()
    # Find the "without arguments" section
    sync_section = text[text.index("Without arguments"):]
    assert "dedup" in sync_section.lower(), (
        "Step 2 (sync mode) must include dedup check before creating Todoist tasks"
    )
    assert "find-tasks" in sync_section, (
        "Step 2 (sync mode) must use find-tasks to check for existing tasks"
    )
    assert "skip" in sync_section.lower(), (
        "Step 2 (sync mode) must skip creation of duplicate tasks"
    )
