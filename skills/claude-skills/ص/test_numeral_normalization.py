"""Regression tests for the ص / salat prayer counter.

History: /ص was a SKILL.md full of inline AppleScript that the model
re-derived every invocation (slow, and each concern — numeral
normalization, date-class row lookup, bounded saves — had to be re-asserted
as prose). 2026-07-21: both skills now shell out to
tools/did/salat-fast.py, which routes through the excel-http daemon client
(lib/neon/excel.py) where the row-lookup and transport concerns live. These
tests pin the script's behavior and both skills' routing to it.
"""
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SKILL_MD = HERE / "SKILL.md"
SALAT_MD = HERE.parent / "salat" / "SKILL.md"
SCRIPT = Path.home() / "i446-monorepo/tools/did/salat-fast.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("salat_fast", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["salat_fast"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_normalizes_non_latin_numerals():
    """Bug (pre-2026-07-21): `/ص ٨` (Arabic-Indic 8) silently failed —
    AppleScript's `as number` only coerces ASCII digits. The script must
    normalize Arabic-Indic, Persian, and CJK numerals before validating."""
    mod = _load_script()
    assert mod.normalize_numeral("8") == 8
    assert mod.normalize_numeral("٨") == 8          # Arabic-Indic
    assert mod.normalize_numeral("۸") == 8          # Persian
    assert mod.normalize_numeral("八") == 8          # CJK
    assert mod.normalize_numeral("٣") == 3
    assert mod.normalize_numeral("三") == 3
    assert mod.normalize_numeral("十") == 10
    assert mod.normalize_numeral(" 5 ") == 5


def test_rejects_non_numbers():
    import pytest
    mod = _load_script()
    with pytest.raises(ValueError):
        mod.normalize_numeral("abc")
    with pytest.raises(ValueError):
        mod.normalize_numeral("")


def test_both_skills_route_to_fast_script():
    """Both /ص and /salat must call salat-fast.py — neither may carry its own
    AppleScript (the pre-2026-07-21 slow path)."""
    for md in (SKILL_MD, SALAT_MD):
        text = md.read_text(encoding="utf-8")
        assert "tools/did/salat-fast.py" in text, f"{md} must run the fast script"
        assert "tell application" not in text, f"{md} must not inline AppleScript"


def test_script_resolves_column_not_hardcoded():
    """Column letters come from neon-cols.json (cols.maybe_col), with AP only
    as the last-resort fallback — a column reshuffle must not break the
    counter silently."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r'maybe_col\("0n",\s*"ص"\)', src)


def test_script_stamps_prayer_marker():
    """The ☀️ build-order stamp (janus/-2n/wakeup/1-1n all read it) must run
    after the write, and a marker failure must not fail the count."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "prayer_marker.py" in src
    assert "never fail the count" in src.lower() or "marker_note" in src


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
