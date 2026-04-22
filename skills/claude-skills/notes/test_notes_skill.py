"""Regression tests for the notes skill (SKILL.md)."""
from pathlib import Path

SKILL_MD = Path(__file__).parent / "SKILL.md"


def test_empty_inbox_still_marks_habit_done():
    """
    Bug: When the inbox was empty, Step 1 said "stop" — so Step 8
    (/did notes) was never reached and the habit never got marked done.

    Fix: Step 1 must skip to Step 8 instead of stopping entirely.
    """
    text = SKILL_MD.read_text()
    # Find the Step 1 instruction about empty inbox
    # It must NOT say "and stop" — it must reference Step 8
    import re
    step1_match = re.search(
        r"nothing to sort.*?\.", text
    )
    assert step1_match, "Step 1 must mention the empty-inbox case"
    step1_sentence = step1_match.group(0)
    assert "stop" not in step1_sentence.lower(), (
        "Step 1 empty-inbox case must not say 'stop' — it must skip to Step 8"
    )
    assert "step 8" in step1_sentence.lower() or "mark" in step1_sentence.lower(), (
        "Step 1 empty-inbox case must reference Step 8 or marking the habit done"
    )
