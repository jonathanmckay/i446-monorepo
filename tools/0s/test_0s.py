#!/usr/bin/env python3
"""Regression tests for the 0s daily-review Excel writer."""
import datetime as dt
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("zeros", Path(__file__).parent / "0s.py")
z = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(z)

TODAY = dt.date(2026, 7, 14)


def test_mdy_matches_sheet_format():
    assert z._mdy(TODAY) == "7/14/26"
    assert z._mdy(dt.date(2026, 1, 1)) == "1/1/26"


def test_motivation_targets_tomorrow_row():
    mot = [f for f in z.FIELDS if f[0] == "motivation"][0]
    assert mot[2] == "D" and mot[3] == "tomorrow"


def test_build_writes_today_and_tomorrow_and_skips_empty():
    ans = {"title": "Good day", "neon": "8", "motivation": "ship it", "win": ""}
    s = z.build_applescript(ans, TODAY)
    # date lookups for both rows present
    assert 'if bv = "7/14/26" then set todayRow to r' in s
    assert 'if bv = "7/15/26" then set tomRow to r' in s
    # text field → today row, quoted
    assert 'set value of range ("E" & todayRow) of ws to "Good day"' in s
    # num field → numeric, no quotes
    assert 'set value of range ("K" & todayRow) of ws to 8.0' in s
    # motivation → tomorrow row, guarded
    assert 'if tomRow > 0 then' in s
    assert 'set value of range ("D" & tomRow) of ws to "ship it"' in s
    # empty field never written
    assert '"H" &' not in s


def test_multiline_and_quotes_escaped():
    s = z.build_applescript({"thankful": 'a "b"\nc'}, TODAY)
    assert '"a \\"b\\"" & linefeed & "c"' in s


def test_non_numeric_num_field_skipped():
    s = z.build_applescript({"neon": "high"}, TODAY)
    assert '"K" &' not in s


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
