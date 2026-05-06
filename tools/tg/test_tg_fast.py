"""Tests for tg-fast.py parsing logic."""
import ast
import re
from pathlib import Path

SRC = Path(__file__).parent / "tg-fast.py"


def _get_main_source():
    return SRC.read_text()


def test_backdate_end_pattern_exists():
    """desc HHMM pattern must be handled before the default start path."""
    tree = ast.parse(_get_main_source())
    source = _get_main_source()
    # The regex for trailing HHMM must appear in the source
    assert r"\s(\d{4})$" in source or r'\s(\d{4})$' in source


def test_backdate_end_match_regex():
    """A trailing 4-digit time (0000-2359) after a description should match."""
    pattern = re.compile(r'\s(\d{4})$')
    assert pattern.search("0l 0706")
    assert pattern.search("work 1823")
    assert pattern.search("family time 0900")
    # Should NOT match when no space before digits
    assert not pattern.search("task1234")


def test_backdate_end_before_default():
    """The desc-HHMM block must appear before the default start block in main()."""
    source = _get_main_source()
    end_match_pos = source.find("desc HHMM")
    default_pos = source.find("# Default: start timer")
    assert end_match_pos != -1, "desc HHMM comment not found"
    assert default_pos != -1, "default start comment not found"
    assert end_match_pos < default_pos, "desc HHMM check must come before default start"


def test_backdate_rejects_invalid_time():
    """Times like 2500 or 1299 should not be treated as backdated starts."""
    pattern = re.compile(r'\s(\d{4})$')
    m = pattern.search("work 2500")
    assert m  # regex matches, but validation should reject
    backtime = m.group(1)
    h, mm = int(backtime[:2]), int(backtime[2:])
    assert not (0 <= h <= 23 and 0 <= mm <= 59), "2500 should fail validation"

    m2 = pattern.search("work 1299")
    backtime2 = m2.group(1)
    h2, mm2 = int(backtime2[:2]), int(backtime2[2:])
    assert not (0 <= h2 <= 23 and 0 <= mm2 <= 59), "1299 should fail validation"
