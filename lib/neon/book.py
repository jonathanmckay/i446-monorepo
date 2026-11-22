"""The Neon workbook without Excel: a seed of every cell plus the ledger's
writes since, evaluated by `neon.calc` (Neon off Excel, 2026-11-22).

  seed(src)        read a COPY of the saved xlsx once and keep every cell's
                   content (formula text or value) in SEED, a pickle beside
                   neon.db. Re-run whenever the workbook file is newer.
  load()           the seed + every write the ledger journaled after the
                   seed's save time, as a calc.Book. Formula cells that
                   depend on a written cell are recomputed here, so the book
                   is current to the last journaled write, not the last save.
  row_for_date / row_for_week / header_col   the lookups the daemon does in
                   Excel today (column of dates, 周 labels, row-1 headers).

Readers only. Nothing here writes to Excel, the daemon or the ledger, and the
ledger is opened read-only. The one thing still fed by Excel is the ledger
itself (the daemon journals a write after it lands in Excel); see the plan
doc for the backend switch that removes that.

Python 3.9-clean (runs on Ix).
"""
from __future__ import annotations

import datetime as _dt
import os
import pickle
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

from neon import calc, ledger

SRC = Path.home() / "Library" / "CloudStorage" / "OneDrive-Personal" / "vault-excel" / "Neon分v12.2.xlsx"
SEED = Path(os.environ.get("NEON_BOOK_SEED") or Path.home() / ".local/share/neon/book-seed.pickle")
SEED_VERSION = 1
# Writes journaled this long before the save are re-applied: a save can be in
# flight while a write lands. Re-applying is harmless, a journaled write
# carries the cell's whole content after it, not a delta.
MARGIN_S = 300

# Where each sheet keeps its day (same table as services/excel-http DATE_COL)
# and its 周 week label.
DATE_COL = {"0分": "B", "0n": "C", "hcbi": "B", "0s897": "B"}
WEEK_COL = {"1n+": "B", "1分+1s": "A", "i9+m5x2": "A"}
_WRITE_KINDS = ("set", "credit", "credit_ref", "text", "lock")


def seed(src: Path = SRC, out: Path = SEED) -> dict:
    """Snapshot the saved workbook into the seed file. Returns its meta."""
    src = Path(src)
    mtime = src.stat().st_mtime
    tmp = Path(tempfile.mkdtemp(prefix="neon-book-"))
    try:
        copy = tmp / "neon.xlsx"
        shutil.copy2(src, copy)                  # never hand openpyxl the file Excel has open
        book, cached = calc.load_xlsx(copy, today=_dt.date.fromtimestamp(mtime))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    meta = {"version": SEED_VERSION, "src": str(src), "src_mtime": mtime,
            "saved": _dt.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
            "cells": len(book.cells), "formulas": len(cached)}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(".part")
    with open(part, "wb") as f:
        pickle.dump({"meta": meta, "cells": book.cells, "arrays": book.arrays, "cached": cached}, f,
                    protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(part, out)
    return meta


def read_seed(path: Path = SEED) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            d = pickle.load(f)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError):
        return None
    return d if (d.get("meta") or {}).get("version") == SEED_VERSION else None


def _content(kind: str, jsonl_kind: str, after_formula, value):
    """A cell's content after a journaled write, or the marker _UNKNOWN.
    `after_formula` is the daemon's read-back of the whole cell."""
    a = after_formula
    if a is None:
        return _UNKNOWN
    if kind == "text":
        return a if a != "" else None
    s = a.strip()
    if s == "":
        return None
    if s.startswith("=") and len(s) > 1:
        return s
    try:
        return float(s)
    except ValueError:
        return a


_UNKNOWN = object()


def writes_since(since_iso: str, conn: Optional[sqlite3.Connection] = None, path: Optional[Path] = None) -> list:
    """[(ts, sheet, col_index, row, content)] for every write journaled after
    `since_iso`, oldest first; content is _UNKNOWN where the journal has no
    read-back (a fallback line). Hand edits the daemon noticed (excel-edit)
    count as writes. Read-only; [] when there is no ledger on this host."""
    own = conn is None
    if own:
        p = Path(path or ledger.DB_PATH)
        if not p.exists():
            return []
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=1.0)
        except sqlite3.Error:
            return []
    try:
        rows = conn.execute(
            """SELECT ts, sheet, col, row, kind, jsonl_kind, after_formula, value FROM events
                WHERE ts > ? AND row IS NOT NULL
                  AND (kind IN (%s) OR jsonl_kind = 'excel-edit')
                ORDER BY ts, id""" % ",".join("?" * len(_WRITE_KINDS)),
            (since_iso,) + _WRITE_KINDS).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if own:
            conn.close()
    return [(ts, sheet, calc.col_index(col), int(row), _content(kind, jk, after, value))
            for ts, sheet, col, row, kind, jk, after, value in rows]


class Live(calc.Book):
    """A Book with where it came from: `meta` (the seed), `through` (ts of
    the newest write applied, or None), `applied`, `unknown` (writes whose
    content the journal does not carry)."""
    meta: dict = {}
    through: Optional[str] = None
    applied: int = 0
    unknown: list = []


def load(seed_path: Path = SEED, conn: Optional[sqlite3.Connection] = None, db_path: Optional[Path] = None,
         upto: Optional[str] = None, today: Optional[_dt.date] = None) -> Optional[Live]:
    """The live book, or None when there is no seed. `upto` (ISO ts) stops
    the replay there: the book as of that moment."""
    d = read_seed(seed_path)
    if d is None:
        return None
    b = Live(dict(d["cells"]), today=today or _dt.date.today(), arrays=d["arrays"])
    b.meta, b.unknown = d["meta"], []
    since = _dt.datetime.fromtimestamp(d["meta"]["src_mtime"] - MARGIN_S).isoformat(timespec="seconds")
    for ts, sheet, col, row, content in writes_since(since, conn, db_path):
        if upto is not None and ts > upto:
            break
        if content is _UNKNOWN:
            b.unknown.append((ts, sheet, calc.col_letter(col), row))
            continue
        if sheet not in b._extent:
            continue
        if content is None:
            b.cells.pop((sheet, col, row), None)
        else:
            b.cells[(sheet, col, row)] = content
        b.applied += 1
        b.through = ts
    b._index()
    return b


def stale(seed_path: Path = SEED, src: Path = SRC) -> bool:
    """The workbook file is newer than the seed (or there is no seed)."""
    d = read_seed(seed_path)
    if d is None:
        return True
    try:
        return Path(src).stat().st_mtime > d["meta"]["src_mtime"] + 1
    except OSError:
        return False                             # no workbook: the seed is all there is


# ── the lookups Excel does for the daemon ────────────────────────────────────

def _text15(v) -> str:
    """A number the way Excel shows it (15 significant digits): 11.3 for the
    11.299999999999999 a =A46+0.1 chain really holds."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return "" if v is None else str(v).strip()
    s = "%.15g" % v
    return s


def row_for_date(book: calc.Book, sheet: str, day: _dt.date) -> Optional[int]:
    col = DATE_COL.get(sheet)
    e = book._extent.get(sheet)
    if col is None or e is None:
        return None
    want, ci = calc.serial(day), calc.col_index(col)
    for row in range(1, e[1] + 1):
        v = book.value(sheet, ci, row)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and int(v) == want:
            return row
    return None


def row_for_week(book: calc.Book, sheet: str, label: str) -> Optional[int]:
    """The row whose 周 cell reads `label` ("11.3"). The last match wins on a
    sheet that carries two years."""
    col = WEEK_COL.get(sheet)
    e = book._extent.get(sheet)
    if col is None or e is None:
        return None
    ci, hit = calc.col_index(col), None
    for row in range(1, e[1] + 1):
        if _text15(book.value(sheet, ci, row)) == str(label).strip():
            hit = row
    return hit


def header_cols(book: calc.Book, sheet: str, row: int = 1) -> dict:
    """{header text: column letter} for a header row (first occurrence wins)."""
    e = book._extent.get(sheet)
    out: dict = {}
    if e is None:
        return out
    for ci in range(1, e[0] + 1):
        v = book.value(sheet, ci, row)
        if isinstance(v, str) and v.strip():
            out.setdefault(v.strip(), calc.col_letter(ci))
    return out


def row_values(book: calc.Book, sheet: str, row: int, first: str = "A", last: Optional[str] = None) -> dict:
    """{column letter: value} for one row (errors as calc.Err, blanks absent)."""
    e = book._extent.get(sheet)
    if e is None:
        return {}
    c2 = calc.col_index(last) if last else e[0]
    out = {}
    for ci in range(calc.col_index(first), c2 + 1):
        v = book.value(sheet, ci, row)
        if v is not None:
            out[calc.col_letter(ci)] = v
    return out
