"""Regression test for the ص skill (SKILL.md)."""
from pathlib import Path

SKILL_MD = Path(__file__).parent / "SKILL.md"


def test_skill_documents_non_latin_numeral_normalization():
    """
    Bug: `/ص ٨` (Arabic-Indic 8) didn't update the spreadsheet. The skill
    substituted `٨` directly into the AppleScript template, and AppleScript's
    `as number` only coerces ASCII digits — the write silently failed.

    Fix: SKILL.md must instruct Claude to normalize non-Latin numerals
    (Arabic-Indic, Persian, CJK) to ASCII digits before substitution.
    """
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "Argument parsing" in text, (
        "SKILL.md must have an 'Argument parsing' section explaining numeral normalization"
    )
    # Each script must be listed with its digit set
    assert "٠١٢٣٤٥٦٧٨٩" in text, "Arabic-Indic digits must be listed for normalization"
    assert "۰۱۲۳۴۵۶۷۸۹" in text, "Persian/Eastern Arabic-Indic digits must be listed"
    assert "零一二三四五六七八九" in text, "CJK digits must be listed"
    # Must instruct ASCII conversion
    assert "0123456789" in text, "Target ASCII digit set must be specified"
    # Must validate before AppleScript handoff
    assert "int(" in text, (
        "SKILL.md must instruct validating the parsed number with Python int() "
        "before passing to AppleScript"
    )
