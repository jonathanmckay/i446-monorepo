"""lib/neon/calc.py — the workbook engine. The full-workbook gate is
tools/neon-ledger/calc_check.py; these pin the Excel semantics it found."""
from __future__ import annotations

import datetime as dt

import pytest

from neon import calc
from neon.calc import Book, Err, col_index as ci


def book(cells, **kw):
    return Book({(s, ci(c), r): v for (s, c, r), v in cells.items()}, **kw)


def val(b, sheet, col, row):
    return b.value(sheet, ci(col), row)


def test_arithmetic_refs_and_cross_sheet():
    b = book({("a", "A", 1): 2.0, ("a", "B", 1): "=A1*3+1", ("n", "A", 1): "='a'!B1^2 & \"x\"",
              ("a", "C", 1): "=10%+A1/4"})
    assert val(b, "a", "B", 1) == 7.0
    assert val(b, "n", "A", 1) == "49x"
    assert val(b, "a", "C", 1) == pytest.approx(0.6)


def test_formula_over_a_blank_is_zero_and_blank_cells_stay_blank():
    b = book({("a", "B", 1): "=A1", ("a", "B", 2): '=IF(A1="","empty","full")', ("a", "B", 3): "=ISBLANK(A1)"})
    assert val(b, "a", "A", 1) is None
    assert val(b, "a", "B", 1) == 0.0
    assert val(b, "a", "B", 2) == "empty" and val(b, "a", "B", 3) is True


def test_aggregates_skip_text_in_references_even_single_cells():
    b = book({("a", "A", 1): 5.0, ("a", "A", 2): "note", ("a", "A", 3): 7.0,
              ("a", "B", 1): "=SUM(A1,A2,A3)", ("a", "B", 2): "=AVERAGE(A1:A3)", ("a", "B", 3): '=SUM(A1,"3")',
              ("a", "B", 4): "=COUNT(A1:A3)", ("a", "B", 5): "=COUNTA(A1:A4)", ("a", "B", 6): "=A1+A2"})
    assert [val(b, "a", "B", r) for r in (1, 2, 3, 4, 5)] == [12.0, 6.0, 8.0, 2.0, 3.0]
    assert val(b, "a", "B", 6) == calc.VALUE


def test_errors_are_values_and_propagate():
    b = book({("a", "A", 1): 0.0, ("a", "B", 1): "=1/A1", ("a", "B", 2): "=SUM(B1,4)", ("a", "B", 3): "=IF(A1=0,9,B1)",
              ("a", "B", 4): "='gone'!A1+1", ("a", "B", 5): '=COUNTIF(INDIRECT("\'gone\'!A1:A3"),"<>")',
              ("a", "B", 6): "=IFERROR(B1,-1)"})
    assert val(b, "a", "B", 1) == calc.DIV0 and val(b, "a", "B", 2) == calc.DIV0
    assert val(b, "a", "B", 3) == 9.0                       # the untaken branch never counts
    assert val(b, "a", "B", 4) == calc.REF and val(b, "a", "B", 5) == calc.REF
    assert val(b, "a", "B", 6) == -1.0


def test_sumproduct_of_not_isblank_weights():
    """0n's score columns: SUMPRODUCT(NOT(ISBLANK(row))*weights)."""
    b = book({("a", "A", 1): 30.0, ("a", "B", 1): 20.0, ("a", "C", 1): 6.0,
              ("a", "A", 2): 1.0, ("a", "C", 2): "x",
              ("a", "D", 2): "=SUMPRODUCT(NOT(ISBLANK(A2:C2))*A1:C1)", ("a", "E", 2): "=SUMPRODUCT(A1:C1,A2:C2)"})
    assert val(b, "a", "D", 2) == 36.0
    assert val(b, "a", "E", 2) == 30.0                      # text counts as 0


def test_countif_sumif_and_sum_range_resizing():
    cells = {("a", "A", r): float(v) for r, v in enumerate([0, 5, 0, 8, 2], start=1)}
    cells.update({("a", "B", 1): '=COUNTIF(A1:A5,"<>0")', ("a", "B", 2): '=SUMIF(A1:A5,">2")',
                  ("a", "B", 3): '=SUMIF(A1:A5,"<>0",A1:A2)', ("a", "B", 4): '=COUNTIFS(A1:A5,">0",A1:A5,"<8")',
                  ("a", "B", 5): '=COUNTIF(A1:A9,"<>")'})
    b = book(cells)
    assert [val(b, "a", "B", r) for r in (1, 2, 3, 4, 5)] == [3.0, 13.0, 15.0, 2.0, 5.0]


def test_indirect_row_window_and_today():
    cells = {("d", "M", r): float(r) for r in range(3, 17)}
    cells[("w", "A", 3)] = '=SUM(INDIRECT("\'d\'!M" & ((ROW()-2)*7+3) & ":M" & ((ROW()-1)*7+2)))'
    cells[("w", "B", 3)] = "=TODAY()-1"
    b = book(cells, today=dt.date(2026, 11, 22))
    assert val(b, "w", "A", 3) == float(sum(range(10, 17)))
    assert val(b, "w", "B", 3) == calc.serial(dt.date(2026, 11, 21))


def test_array_formula_and_mmult():
    b = Book({("a", 1, 1): 1.0, ("a", 2, 1): 2.0, ("a", 1, 2): 3.0, ("a", 2, 2): 4.0,
              ("a", 4, 1): "=SUM(A1:B1*A2:B2)", ("a", 4, 2): "=SUM(MMULT(A1:B2,A1:B2))"},
             arrays={("a", 4, 1): (4, 1), ("a", 4, 2): (4, 2)})
    assert b.value("a", 4, 1) == 11.0
    assert b.value("a", 4, 2) == 54.0


def test_a_write_invalidates_dependents():
    b = book({("a", "A", 1): 1.0, ("a", "B", 1): "=A1+1", ("a", "C", 1): "=SUM(A1:B1)"})
    assert val(b, "a", "C", 1) == 3.0
    b.set("a", ci("A"), 1, "=10+5")
    assert val(b, "a", "C", 1) == 31.0
    b.set("a", ci("A"), 1, None)
    assert val(b, "a", "C", 1) == 1.0


def test_circular_reference_does_not_hang():
    b = book({("a", "A", 1): "=B1", ("a", "B", 1): "=A1"})
    assert isinstance(val(b, "a", "A", 1), Err)


def test_unsupported_function_raises_calc_error():
    b = book({("a", "A", 1): "=VLOOKUP(1,B1:C2,2)"})
    with pytest.raises(calc.CalcError):
        val(b, "a", "A", 1)
