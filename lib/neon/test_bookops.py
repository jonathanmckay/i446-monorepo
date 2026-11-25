"""lib/neon/bookops.py — the daemon's cell operations on the book. Pinned to
what Excel echoed in the journal (Oct–Nov 2026)."""
from __future__ import annotations

import datetime as dt

import pytest

from neon import bookops, calc
from neon.calc import col_index as ci


def test_formula_text_matches_excels_read_back():
    assert bookops.formula_text(16.0) == "16"
    assert bookops.formula_text(253.28571428571428) == "253.285714285714"
    assert bookops.formula_text(-90.0) == "-90"
    assert bookops.formula_text(4.547473508865e-13) == "4.547473508865E-13"
    assert bookops.formula_text("='0n'!BK276 + 0+15") == "='0n'!BK276 + 0+15"     # spacing is kept
    assert bookops.formula_text(None) == "" and bookops.formula_text("mocha") == "mocha"


def test_value_text_matches_applescripts_as_string():
    assert bookops.value_text(17.0) == "17.0" and bookops.value_text(0.0) == "0.0"
    assert bookops.value_text(1000 / 3) == "333.333333333333"                       # 12 decimals
    assert bookops.value_text(1501.9285714285713) == "1501.928571428571"
    assert bookops.value_text(6.5) == "6.5"
    assert bookops.value_text(4.547473508865e-13) == "4.547473508865E-13"
    assert bookops.value_text(None) == "" and bookops.value_text(calc.REF) == "missing value"
    assert bookops.value_text("bread + egg") == "bread + egg"


@pytest.mark.parametrize("before, bv, kind, value, mode, after", [
    ("", "", "write", "=13+4", None, "=13+4"),
    ("=13+4", "17.0", "write", "469", None, "469"),                 # a numeric string becomes a number
    ("5", "5.0", "write", "-90", None, "-90"),
    ("7", "7.0", "write", "", None, ""),                            # cleared
    ("", "", "append", "+480", None, "=480"),
    ("0", "0.0", "append", "+3", None, "=3"),
    ("='0n'!AZ327", "0.0", "append", "+5", None, "='0n'!AZ327+5"),
    ("2", "2.0", "append", "+2", None, "=2+2"),                     # a bare number is made a formula first
    ("=10", "10.0", "append", "-7.5", None, "=10-7.5"),
    ("", "", "append", ", mocha", None, "mocha"),
    ("mocha", "mocha", "append", ", 3 apples", None, "mocha, 3 apples"),
    ("", "", "write", 30, "add", "30"),
    ("26", "26.0", "write", 30, "add", "56"),                       # old + N, from the rendered old value
    ("=20+6", "26.0", "write", 4, "add", "30"),                     # and a formula is replaced by the sum
    ("", "", "write", "=not a formula", "text", "=not a formula"),
    ("", "", "write", 1, None, "1"),
])
def test_content_for_reproduces_the_daemons_setters(before, bv, kind, value, mode, after):
    assert bookops.formula_text(bookops.content_for(before, bv, kind, value, mode)) == after


def test_mode_add_on_text_fails_as_the_script_does():
    with pytest.raises(ValueError):
        bookops.content_for("note", "note", "write", 5, "add")


def test_apply_returns_the_four_echoes_and_moves_dependents():
    b = calc.Book({("0分", ci("P"), 324): "=13+4", ("0分", ci("D"), 324): "=SUM(P324:Y324)"})
    assert bookops.apply(b, "0分", "P", 324, "write", "=13+7") == ("=13+4", "17.0", "20.0", "=13+7")
    assert bookops.read(b, "0分", "D", 324) == ("20.0", "=SUM(P324:Y324)")
    assert bookops.apply(b, "0分", "Q", 324, "append", "+10") == ("", "", "10.0", "=10")
    assert bookops.read(b, "0分", "D", 324)[0] == "30.0"
    assert bookops.apply(b, "0分", "Q", 324, "write", "") == ("=10", "10.0", "", "")
    assert b.content("0分", ci("Q"), 324) is None                   # cleared means absent, so ISBLANK is true


def test_find_row_dates_and_week_labels():
    s = calc.serial
    cells = {("0n", ci("C"), 5): s(dt.date(2026, 1, 3)), ("0n", ci("C"), 327): s(dt.date(2026, 11, 22)),
             ("0s897", ci("B"), 10): s(dt.date(2025, 1, 3)), ("0s897", ci("B"), 400): s(dt.date(2026, 1, 3)),
             ("1分+1s", ci("A"), 46): 11.2, ("1分+1s", ci("A"), 47): "=A46+0.1", ("1n+", ci("B"), 52): "11.4"}
    b = calc.Book(cells)
    assert bookops.find_row(b, "0n", "11/22") == 327 and bookops.find_row(b, "0n", "2026-11-22") == 327
    assert bookops.find_row(b, "0n", "12/25") is None
    assert bookops.find_row(b, "0s897", "1/3") == 10                # first match, as the script's scan
    assert bookops.find_row(b, "0s897", "1/3/26") == 400            # a year picks the right January
    assert bookops.find_row(b, "1分+1s", "11.3") == 47               # 11.299999999999999 reads 11.3
    assert bookops.find_row(b, "1n+", "11.4") == 52 and bookops.find_row(b, "分ledger", "11/22") is None


# ── /read_range from the book (2026-11-25) ───────────────────────────────────

def test_date_text_is_applescripts_long_form():
    s = calc.serial(dt.date(2026, 11, 25))
    assert bookops.date_text(s) == "Wednesday, November 25, 2026 at 12:00:00\u202fAM"
    assert bookops.date_text(s + 0.75) == "Wednesday, November 25, 2026 at 6:00:00\u202fPM"
    assert bookops.date_text(calc.serial(dt.date(2026, 3, 1))) == "Sunday, March 1, 2026 at 12:00:00\u202fAM"


def test_range_text_forms():
    assert bookops.range_text(None) == ""
    assert bookops.range_text(calc.Err("#DIV/0!")) == ""          # the daemon maps "missing value" to ""
    assert bookops.range_text(60.0) == "60.0"
    assert bookops.range_text(1.7142857142857142) == "1.714285714286"
    assert bookops.range_text("Th") == "Th"
    assert bookops.range_text(46351.0) == "4.6351E+4"             # a number that is not date-formatted
    assert bookops.range_text(46351.0, True).startswith("Wednesday, November 25, 2026")


def test_read_range_rows_and_dates():
    d = calc.serial(dt.date(2026, 11, 25))
    b = calc.Book({("hcbi", 2, 3): d, ("hcbi", 2, 4): "=B3+1", ("hcbi", 3, 3): "W", ("hcbi", 4, 3): 2.0,
                   ("hcbi", 4, 4): "=D3/0"})
    b.dates = {("hcbi", 2, 3), ("hcbi", 2, 4)}
    assert bookops.read_range(b, "hcbi", "b3:d4") == [
        ["Wednesday, November 25, 2026 at 12:00:00\u202fAM", "W", "2.0"],
        ["Thursday, November 26, 2026 at 12:00:00\u202fAM", "", ""],
    ]
    b.dates = None                                                # an older seed: serials, not text
    assert bookops.read_range(b, "hcbi", "B3:B3") == [[bookops.value_text(d)]]
    assert bookops.read_range(b, "hcbi", "B3") is None


def test_value_text_matches_applescript_real_to_text():
    """Every pair below is `osascript -e '(<number>) as string'` on Ix, 2026-11-25."""
    for number, text in [
        (9999.5, "9999.5"), (10000.0, "1.0E+4"), (12091.0, "1.2091E+4"),
        (10795.428571428572, "1.079542857143E+4"), (20000.0, "2.0E+4"), (123456789.0, "1.23456789E+8"),
        (1234567.891, "1.234567891E+6"), (-15000.25, "-1.500025E+4"), (-9999.0, "-9999.0"),
        (0.001, "1.0E-3"), (0.0010001, "0.0010001"), (0.002, "0.002"), (0.0005, "5.0E-4"),
        (0.00012345, "1.2345E-4"), (0.000999999, "9.99999E-4"), (0.01, "0.01"), (2.5e-7, "2.5E-7"),
        (0.1234567890123456, "0.123456789012"), (123.4567890123456789, "123.456789012346"),
        (60.0, "60.0"), (0.0, "0.0"), (1.5e20, "1.5E+20"),
    ]:
        assert bookops.value_text(number) == text, number
