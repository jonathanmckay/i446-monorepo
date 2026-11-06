#!/usr/bin/env python3
"""Regression (2026-11-06): "I look at column T in 1n+ and it's still blank".

T49 held a 1. Column T's font is white by design, readable only on the dark
fill its conditional format paints, and that rule was `> 1`: fine for the
minutes the column used to hold, but did-fast now writes a 1, which got no
fill and rendered white on white. Excel values and the ledger all looked right.

The invariant, read from the real workbook: a mark of 1 in a week row is never
white text with nothing behind it.
"""
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")
from openpyxl.utils import get_column_letter  # noqa: E402

NEON = Path.home() / "OneDrive/vault-excel/Neon分v12.2.xlsx"
FIRST_WEEK_ROW, LAST_WEEK_ROW = 6, 57
FIRST_COL, LAST_COL = 3, 38  # C..AL


def _white(font) -> bool:
    c = font.color
    if c is None:
        return False
    return c.theme == 0 if c.type == "theme" else c.rgb == "FFFFFFFF"


def _fires_on_one(rule) -> bool:
    if rule.type != "cellIs" or not (rule.dxf and rule.dxf.fill):
        return False
    args = [float(f) for f in rule.formula]
    return {
        "greaterThan": lambda: 1 > args[0],
        "greaterThanOrEqual": lambda: 1 >= args[0],
        "equal": lambda: args[0] == 1,
        "between": lambda: args[0] <= 1 <= args[1],
    }.get(rule.operator, lambda: False)()


def invisible_marks(ws) -> list:
    painted = set()
    for cf in ws.conditional_formatting:
        if any(_fires_on_one(r) for r in cf.rules):
            for rng in cf.sqref.ranges:
                painted.update(c for c in rng.cells)
    bad = []
    for col in range(FIRST_COL, LAST_COL + 1):
        for row in range(FIRST_WEEK_ROW, LAST_WEEK_ROW + 1):
            cell = ws.cell(row, col)
            if _white(cell.font) and cell.fill.fill_type is None and (row, col) not in painted:
                bad.append(f"{get_column_letter(col)}{row}")
    return bad


@pytest.mark.skipif(not NEON.exists(), reason="Neon workbook not on this machine")
def test_a_mark_of_one_is_never_white_on_white():
    ws = openpyxl.load_workbook(NEON)["1n+"]
    assert invisible_marks(ws) == []
