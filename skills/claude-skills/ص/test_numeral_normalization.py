"""Regression tests for the ص skill (SKILL.md)."""
import re
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


def _applescript_blocks(text: str) -> list[str]:
    """Extract every ```bash ... ``` fenced code block that actually contains
    an osascript/AppleScript payload (skips unrelated bash snippets, e.g. the
    prayer_marker.py invocation)."""
    blocks = re.findall(r"```bash\n(.*?)\n```", text, re.DOTALL)
    return [b for b in blocks if "osascript" in b]


def test_no_active_workbook_reference():
    """Regression (2026-07-20): 'sheet "0n" of active workbook' silently
    targets whatever file happens to be frontmost on Ix (an unattended
    machine) instead of the Neon workbook — same bug class already fixed in
    lib/neon/excel.py and services/excel-http/server.py earlier the same day."""
    text = SKILL_MD.read_text(encoding="utf-8")
    for block in _applescript_blocks(text):
        assert "active workbook" not in block, (
            "must pin to workbook \"Neon分v12.2.xlsx\" explicitly, never 'active workbook'"
        )
        assert 'workbook "Neon分v12.2.xlsx"' in block, (
            "each AppleScript block must reference the Neon workbook explicitly"
        )


def test_row_lookup_uses_bulk_range_read_not_per_cell_loop():
    """Regression (2026-07-20): the old lookup did
    `repeat with i from 2 to 500: if (string value of cell ("C" & i) ...)` —
    up to 500 individual cross-process cell reads, observed live taking 80s+.
    Fix: one bulk `value of range "C2:C500"` call, then iterate the returned
    list locally (sub-second)."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert 'value of range "C2:C500"' in text, (
        "row lookup must use a single bulk range read, not a per-cell loop"
    )
    for block in _applescript_blocks(text):
        assert 'string value of cell ("C" & i)' not in block, (
            "must not fall back to the slow per-cell string-compare loop"
        )


def test_row_lookup_is_date_class_aware():
    """Regression (2026-07-20): column C holds real Excel date values, not
    'M/D' text. `(string value of cell (...)) = "M/D"` never matches a real
    date, so the write silently reported 'no row for <date>' even though the
    row existed — observed live on 2026-07-20's own row. Fix: compare by
    month/day when the value is a real date, falling back to text match."""
    text = SKILL_MD.read_text(encoding="utf-8")
    for block in _applescript_blocks(text):
        if 'value of range "C2:C500"' not in block:
            continue
        assert "month of cv as integer" in block and "day of cv" in block, (
            "row lookup must compare a real date value by month/day, not "
            "just as a formatted string"
        )


def test_save_is_time_bounded_and_reported_separately():
    """Regression (2026-07-20): `save active workbook` (now `save wb`) can
    hang for 80s+ (observed live — Excel's cloud-file save coordination with
    an unhealthy OneDrive file-provider). The old script had no timeout, so a
    hung save meant the whole ritual silently blocked with no signal to the
    user that anything was wrong ("failed silently"). Fix: bound the save in
    `with timeout of 15 seconds`, and return the save outcome separately from
    the write outcome so a timeout is visible, not swallowed."""
    text = SKILL_MD.read_text(encoding="utf-8")
    for block in _applescript_blocks(text):
        if 'value of range "C2:C500"' not in block:
            continue
        assert "with timeout of 15 seconds" in block, (
            "save must be wrapped in a bounded timeout so a hang fails fast"
        )
        assert "SAVE_TIMEOUT" in block, (
            "a timed-out save must be reported distinctly, not silently "
            "treated the same as a successful save"
        )
        assert re.search(r'return .*"\|".*saveStatus', block), (
            "the write result and save outcome must both be returned, "
            "separated, so a save failure can't hide behind a clean-looking N"
        )
    assert "SAVE_TIMEOUT" in text and "saveStatus" in text.split("## Output")[0], (
        "the skill body (before the Output section) must document how to "
        "read a save-timeout result, not just produce it silently"
    )
