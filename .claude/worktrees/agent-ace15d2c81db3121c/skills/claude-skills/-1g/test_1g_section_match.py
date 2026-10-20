"""Regression tests for -1g skill (SKILL.md)."""
from pathlib import Path

SKILL_MD = Path(__file__).parent / "SKILL.md"


def test_section_match_is_prefix_not_exact():
    """
    Bug: The build order heading mutated from '## -1₲' to '## -1₲a',
    causing the -1g skill to silently fail to find the section.
    Goals written for a time block (e.g. 巳) were never inserted.

    Fix: Step 3 must match the heading as a prefix (startswith),
    not an exact string match, so trailing chars don't break it.
    """
    text = SKILL_MD.read_text()
    step3 = text[text.index("Step 3"):text.index("Step 4")]
    assert "starting with" in step3.lower() or "ignoring trailing" in step3.lower(), (
        "Step 3 must use prefix/startswith matching for the ## -1₲ heading, "
        "not exact match, to handle trailing character mutations"
    )


def test_section_not_found_errors_explicitly():
    """
    The skill must surface a clear error if the -1₲ section is missing,
    not silently write nothing.
    """
    text = SKILL_MD.read_text()
    step3 = text[text.index("Step 3"):text.index("Step 4")]
    assert "error" in step3.lower() or "not found" in step3.lower(), (
        "Step 3 must error explicitly if -1₲ section is not found"
    )
