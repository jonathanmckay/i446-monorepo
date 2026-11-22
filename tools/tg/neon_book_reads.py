#!/usr/bin/env python3
"""neon_book_reads: janus's two Neon reads, answered without Excel.

2026-11-22, "running without using excel". fetch_points and
fetch_habits_today each sent one AppleScript to Excel on Ix and parsed its
text reply. These functions build the SAME reply from the Neon book
(lib/neon/book: the seeded workbook + every write the ledger journaled since,
computed by lib/neon/calc), so janus's parsing, its torn-read guards and its
tests stay exactly as they are and only the transport changes.

Each returns None when the book cannot answer (no seed on this host, the seed
is behind a workbook Excel is still saving, a journaled write whose content is
unknown, the day's row not found): janus then falls back to the AppleScript.

No prompt_toolkit, no Excel, no daemon. `raw_*` take a book so tests need no
ledger.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))

# The seed may trail the saved workbook by this much before the book is
# considered behind (the reseed job runs every 30 min; a hand edit typed into
# Excel is invisible to the ledger until then).
MAX_SEED_LAG_S = 2 * 3600
_cache: dict = {"key": None, "book": None, "at": 0.0}
# One book, several janus threads (points, habits, the tabs' ticker): a Book
# memoises and tracks its evaluation stack, so readers take turns.
_lock = threading.RLock()
RELOAD_S = 2.0


def live_book():
    """The current book, or None (see the module docstring). Reloaded when
    the seed or the ledger changed, at most every RELOAD_S."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("NEON_TEST_DB_OK"):
        return None
    if os.environ.get("NEON_BOOK_READS", "1") in ("0", "off"):
        return None
    try:
        from neon import book, ledger
        now = time.monotonic()
        try:
            key = (book.SEED.stat().st_mtime, ledger.DB_PATH.stat().st_mtime,
                   Path(str(ledger.DB_PATH) + "-wal").stat().st_mtime if Path(str(ledger.DB_PATH) + "-wal").exists() else 0)
        except OSError:
            return None
        if _cache["book"] is not None and (key == _cache["key"] or now - _cache["at"] < RELOAD_S):
            return _cache["book"]
        b = book.load()
        if b is None or b.unknown:
            return None
        try:
            lag = book.SRC.stat().st_mtime - b.meta["src_mtime"]
        except OSError:
            lag = 0                                # no workbook on disk: the book is the workbook
        if lag > MAX_SEED_LAG_S:
            return None
        _cache.update(key=key, book=b, at=now)
        return b
    except Exception:
        return None


def _txt(v) -> str:
    """A cell value the way the AppleScript's `as text` handed it over:
    numbers parse with float(), blanks and errors are empty."""
    from neon import calc
    if v is None or isinstance(v, calc.Err):
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _formula_txt(book, sheet, ci, row) -> str:
    c = book.content(sheet, ci, row)
    return "" if c is None else (c if isinstance(c, str) else _txt(c))


def raw_points(book, day: dt.date):
    """fetch_points' reply: D | G:O formulas | P:Y values | G:O values."""
    from neon import book as nb, calc
    row = nb.row_for_date(book, "0分", day)
    if row is None:
        return None
    ci = calc.col_index
    parts = [_txt(book.value("0分", ci("D"), row))]
    parts += [_formula_txt(book, "0分", c, row) for c in range(ci("G"), ci("O") + 1)]
    parts += [_txt(book.value("0分", c, row)) for c in range(ci("P"), ci("Y") + 1)]
    parts += [_txt(book.value("0分", c, row)) for c in range(ci("G"), ci("O") + 1)]
    return "|".join(parts)


def _two_rows(book, row: int) -> str:
    from neon import calc
    ci = calc.col_index
    return "|".join("".join(_txt(book.value("hcbi", c, r)) + "," for c in range(ci("E"), ci("Y") + 1))
                    for r in (row, row + 1))


def _label_row(book, test):
    from neon import calc
    for r in range(300, 451):
        if test(book.value("hcbi", calc.col_index("B"), r)):
            return r
    return None


def raw_habits(book, day: dt.date, ytd_cells, total_cell: str):
    """fetch_habits_today's reply:
    <hdr>\\t<val>| ... || <ytd>| ... || <dozen>[;<prev>][~<annual>][#<total>] ||kcal=<N>"""
    from neon import book as nb, calc
    ci = calc.col_index
    row = nb.row_for_date(book, "0n", day)
    if row is None:
        return None
    out = "".join(_txt(book.value("0n", c, 1)) + "\t" + _txt(book.value("0n", c, row)) + "|"
                  for c in range(ci("D"), ci("AS") + 1))
    out += "|"
    for cell in ytd_cells:
        col = cell.rstrip("0123456789")
        out += _txt(book.value("0n", ci(col), int(cell[len(col):]))) + "|"
    out += "|"
    qn = (day.month - 1) // 3 + 1
    q_row = _label_row(book, lambda v: v == f"Q{qn}")
    if q_row:
        out += _two_rows(book, q_row)
    if qn > 1:
        p_row = _label_row(book, lambda v: v == f"Q{qn - 1}")
        if p_row:
            out += ";" + _two_rows(book, p_row)
    a_row = _label_row(book, lambda v: isinstance(v, float) and int(v) == day.year)
    if a_row:
        out += "~" + _two_rows(book, a_row)
    tcol = total_cell.rstrip("0123456789")
    out += "#" + _txt(book.value("hcbi", ci(tcol), int(total_cell[len(tcol):])))
    k_row = nb.row_for_date(book, "hcbi", day)
    out += "||kcal=" + (_txt(book.value("hcbi", ci("U"), k_row)) if k_row else "")
    return out


def row(sheet: str, excel_row: int, cols):
    """({column letter: value}, "HH:MM:SS" of the newest write applied) for
    one row as the book computes it, for the Neon tabs; (None, None) when the
    book cannot answer. Blanks are "", errors their code (#REF!); the sheet's
    date column is left out (it never changes and the grid formats it)."""
    with _lock:
        b = live_book()
        if b is None:
            return None, None
        try:
            from neon import book as nb, calc
            skip = nb.DATE_COL.get(sheet)
            out = {}
            for c in cols:
                if c == skip:
                    continue
                v = b.value(sheet, calc.col_index(c), excel_row)
                out[c] = "" if v is None else v.code if isinstance(v, calc.Err) else v
            return out, (b.through or "")[11:19] or None
        except Exception:
            return None, None


_reseed = {"at": 0.0}
RESEED_EVERY_S = 30 * 60


def reseed_due() -> bool:
    """Cheap gate for a ticker: no I/O."""
    return not _reseed["at"] or time.monotonic() - _reseed["at"] >= RESEED_EVERY_S


def maybe_reseed(run=None) -> bool:
    """While Excel is still saving the workbook: every RESEED_EVERY_S, when the
    file is newer than the seed, run book_check.py check --reseed (compare the
    book with the save, log any hand edit, adopt the save). A subprocess: the
    openpyxl loads are ~2 s and ~140 MB. True when one ran."""
    if os.environ.get("PYTEST_CURRENT_TEST") and run is None:
        return False
    now = time.monotonic()
    if _reseed["at"] and now - _reseed["at"] < RESEED_EVERY_S:
        return False
    try:
        from neon import book
        _reseed["at"] = now
        if not book.SEED.exists() or not book.stale():
            return False
        import subprocess
        (run or subprocess.run)(
            ["/usr/bin/python3", str(Path.home() / "i446-monorepo/tools/neon-ledger/book_check.py"), "check", "--reseed"],
            capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
        return True
    except Exception:
        return False


REMOTE_TIMEOUT_S = 8


def _ask_ix(args: list, run=None):
    """Off Ix there is no book (the seed and the ledger live on Ix): ask Ix's
    copy of this module over ssh. None on any failure, and under pytest."""
    if run is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    if os.environ.get("NEON_BOOK_READS", "1") in ("0", "off"):
        return None
    try:
        import subprocess
        import neon_tabs
        if run is None and neon_tabs.on_ix():
            return None                              # here the local book already said no
        r = (run or subprocess.run)(
            ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
             os.environ.get("IX_HOST", "ix"), "/usr/bin/python3 ~/i446-monorepo/tools/tg/neon_book_reads.py " + " ".join(args)],
            capture_output=True, text=True, timeout=REMOTE_TIMEOUT_S, stdin=subprocess.DEVNULL)
        out = (r.stdout or "").strip("\n")
        if r.returncode != 0 or not out.startswith("OK\t"):
            return None
        return out[3:]
    except Exception:
        return None


def points(day: dt.date, run=None):
    with _lock:
        b = live_book()
        try:
            if b is not None:
                return raw_points(b, day)
        except Exception:
            return None
    return _ask_ix(["points", day.isoformat()], run)


def habits(day: dt.date, ytd_cells, total_cell: str, run=None):
    with _lock:
        b = live_book()
        try:
            if b is not None:
                return raw_habits(b, day, ytd_cells, total_cell)
        except Exception:
            return None
    return _ask_ix(["habits", day.isoformat(), ",".join(ytd_cells), total_cell], run)


def main(argv) -> int:
    """points YYYY-MM-DD | habits YYYY-MM-DD AQ375,AR375 X388 → "OK\t<reply>"
    (the reply janus parses), or exit 1 when the book cannot answer."""
    os.environ.setdefault("NEON_TEST_DB_OK", "1")
    try:
        day = dt.date.fromisoformat(argv[1])
        b = live_book()
        out = None
        if b is not None:
            out = raw_points(b, day) if argv[0] == "points" else raw_habits(b, day, argv[2].split(","), argv[3])
    except Exception as e:
        print("error: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1
    if out is None:
        return 1
    sys.stdout.write("OK\t" + out + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
