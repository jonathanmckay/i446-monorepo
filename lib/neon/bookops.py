"""What the excel-http daemon's AppleScript does to a cell, done to the Neon
book instead (Neon off Excel, backend step, 2026-11-22).

The daemon answers every write with four strings read back from Excel: the
cell's formula and value before, and its value and formula after. These
functions produce the same four from a `calc.Book`, so the journal can be
used to check them (tools/neon-ledger/backend_shadow.py) long before
anything answers a caller from the book.

  formula_text(content)   AppleScript `formula of theCell`
  value_text(value)       AppleScript `(value of theCell) as string`
  content_for(...)        the cell's new content for a request
  apply(book, ...)        do it → (before, before_value, after_value, after)
  find_row(book, ...)     the daemon's lookup_row

Pinned to journaled reality (Oct–Nov 2026, 6,981 writes): formulas read back
byte-identical; a number reads back without ".0" as a formula and with it as
a value; values carry at most 12 decimals; an error cell's value is
"missing value". Mode "add" adds to the RENDERED old value, as the script
does. Python 3.9-clean (runs on Ix).
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from neon import calc, replay

_NUMERIC = re.compile(r"^\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*$")


def formula_text(content) -> str:
    """`formula of theCell`: a formula as typed, a number in Excel's general
    form (16, 253.285714285714, 4.547473508865E-13), text as is, blank ""."""
    if content is None:
        return ""
    if isinstance(content, bool):
        return "TRUE" if content else "FALSE"
    if isinstance(content, (int, float)):
        f = float(content)
        if f == int(f) and abs(f) < 1e15:
            return str(int(f))
        s = "%.15G" % f
        if "E" in s:
            m, e = s.split("E")
            return "%sE%s%d" % (m, "-" if e.startswith("-") else "+", abs(int(e)))
        return s
    if isinstance(content, calc.Err):
        return content.code
    return str(content)


def value_text(value) -> str:
    """`(value of theCell) as string`."""
    if value is None:
        return ""
    if isinstance(value, calc.Err):
        return "missing value"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        f = float(value)
        if f == int(f) and abs(f) < 1e15:
            return "%d.0" % int(f)
        if f != 0 and abs(f) < 1e-4:
            m, e = ("%.12E" % f).split("E")
            m = m.rstrip("0").rstrip(".")
            return "%sE%s%d" % (m, "-" if e.startswith("-") else "+", abs(int(e)))
        s = ("%.12f" % f).rstrip("0")
        return s + "0" if s.endswith(".") else s
    return str(value)


def infer(text: str):
    """Excel's type for a string handed to `set value of theCell to "<text>"`:
    a number when it reads as one, else the text. (No dates, booleans or
    percentages occur in two months of requests; they stay text here and the
    shadow reports the difference if one ever arrives.)"""
    if isinstance(text, str) and _NUMERIC.match(text):
        try:
            return float(text)
        except ValueError:
            pass
    return text


def _blank(s) -> bool:
    return s in ("", "0")


def content_for(before_formula: str, before_value: str, kind: str, value, mode: Optional[str] = None):
    """The content a cell holds after a request, given what the script sees
    first (its formula and rendered value). kind: "write" | "append".
    mode (kind write only): None/"set" | "add" | "text".
    → a formula string ("=…"), a float, text, or None for a cleared cell."""
    if kind == "append" or mode == "append":
        after = replay.apply(before_formula, "append", value)
        return after if after.startswith("=") else (infer(after) if after != "" else None)
    if kind != "write":
        raise ValueError(kind)
    if mode == "text":
        s = "" if value is None else str(value)
        return s if s != "" else None
    if mode == "add":
        n = float(value)
        if _blank(before_value):
            return n
        return float(before_value) + n            # ValueError on text: the script errors there too
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    s = "" if value is None else str(value)
    if s == "":
        return None
    if s.startswith("=") and len(s) > 1:
        return s
    return infer(s)


def read(book: calc.Book, sheet: str, col: str, row: int) -> tuple:
    """(value text, formula text) of one cell, as the daemon's /read."""
    ci = calc.col_index(col.upper())
    return value_text(book.value(sheet, ci, row)), formula_text(book.content(sheet, ci, row))


def apply(book: calc.Book, sheet: str, col: str, row: int, kind: str, value,
          mode: Optional[str] = None) -> tuple:
    """Apply one request to the book.
    → (before formula, before value, after value, after formula), all text."""
    ci = calc.col_index(col.upper())
    before_value, before = read(book, sheet, col, row)
    book.set(sheet, ci, row, content_for(before, before_value, kind, value, mode))
    after_value, after = read(book, sheet, col, row)
    return before, before_value, after_value, after


# ── row lookup ───────────────────────────────────────────────────────────────

DATE_COL = {"0分": "B", "0n": "C", "1n+": "B", "hcbi": "B", "0s897": "B", "1分+1s": "A", "i9+m5x2": "A"}
LABEL_SHEETS = {"1n+", "1分+1s", "i9+m5x2"}
_MD = re.compile(r"^(\d{1,2})/(\d{1,2})$")
_MDY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def date_target(date_str) -> tuple:
    """(month, day, year|None) for a date-shaped target, else (None, None, None)."""
    s = str(date_str).strip()
    m = _MD.match(s)
    if m:
        return int(m.group(1)), int(m.group(2)), None
    m = _MDY.match(s)
    if m:
        y = int(m.group(3))
        return int(m.group(1)), int(m.group(2)), (2000 + y if y < 100 else y)
    m = _ISO.match(s)
    if m:
        return int(m.group(2)), int(m.group(3)), int(m.group(1))
    return None, None, None


def find_row(book: calc.Book, sheet: str, date_str) -> Optional[int]:
    """The daemon's lookup_row: first row in 2..800 whose date-column cell is
    that day (a real date matches by month/day, and year when one is given)
    or reads as the target text (a week label "11.3", at Excel's 15
    significant digits). On the daily sheets the column holds real dates,
    which the seed keeps as serial numbers; on the label sheets, numbers are
    labels."""
    col = DATE_COL.get(sheet)
    if not col:
        return None
    ci = calc.col_index(col)
    target = str(date_str).strip()
    month, day, year = date_target(target)
    dated = sheet not in LABEL_SHEETS
    for row in range(2, 801):
        v = book.value(sheet, ci, row)
        if v is None:
            continue
        if dated and isinstance(v, (int, float)) and not isinstance(v, bool) and 20000 < v < 80000:
            if month is None:
                continue
            d = calc._EPOCH + _dt.timedelta(days=int(v))
            if d.month == month and d.day == day and (year is None or d.year == year):
                return row
            continue
        text = formula_text(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
        if text == target:
            return row
    return None
