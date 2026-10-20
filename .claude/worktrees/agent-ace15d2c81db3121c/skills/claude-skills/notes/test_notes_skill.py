"""Regression tests for the notes skill (SKILL.md)."""
from pathlib import Path

SKILL_MD = Path(__file__).parent / "SKILL.md"


def test_empty_inbox_still_marks_habit_done():
    """
    Bug: When the inbox was empty, Step 1 said "stop" — so the final
    step (mark notes habit done) was never reached and the habit never
    got marked done. Regressed 2026-06-27 when Steps 7-8 (action items,
    archive) were inserted and renumbered "mark habit done" to Step 9,
    but Step 1's empty-inbox wording reverted to "stop" (fixed 2026-09-15).

    Fix: Step 1 must skip to the mark-habit-done step instead of stopping
    entirely, whatever that step is currently numbered.
    """
    text = SKILL_MD.read_text()
    # Find the Step 1 instruction about empty inbox
    # It must NOT say "and stop" — it must reference marking the habit done
    import re
    step1_match = re.search(
        r"nothing to sort.*?\.", text
    )
    assert step1_match, "Step 1 must mention the empty-inbox case"
    step1_sentence = step1_match.group(0)
    assert "stop" not in step1_sentence.lower(), (
        "Step 1 empty-inbox case must not say 'stop' — it must skip to the "
        "mark-habit-done step"
    )
    assert re.search(r"step \d+", step1_sentence.lower()) or "mark" in step1_sentence.lower(), (
        "Step 1 empty-inbox case must reference a specific step number or "
        "marking the habit done"
    )
