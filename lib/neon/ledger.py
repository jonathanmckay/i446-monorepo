"""Neon semantic ledger — the SQLite store that will replace the Excel workbook.

Step 1 of "Neon off Excel" (JM, Todoist 6hp23H, 2026-10-19): DOUBLE WRITE.
Every entry the excel-http daemon journals to the JSONL audit log
(~/vault/g245/neon-ledger/YYYY-MM.jsonl) is also recorded here, classified by
what the caller MEANT rather than by what the cell's value did:

  credit      /append of a literal "+15" / "-45"       → points = the literal
  credit_ref  /append of a reference "+'1n+'!C5"       → points = after-before
                                                         when both values are
                                                         numeric, else NULL
  set         /write of a value or formula ("=13+7")   → after_value is the
                                                         cell's truth; no points
  lock        build-order-daemon block-turnover lock   → snapshot of a per-block
              (G:O formula frozen to its value)          residual, no points
  text        any non-numeric append/write (hcbi food, review prose)
  observe     reconcile / baseline / excel-edit        → an observed cell value
                                                         with no request behind it

Why not "points = after_value - before_value" (the obvious primitive): most 0分
domain cells are `='0n'!BF<row> + literals` — their value moves whenever a 0n
habit is marked, which never passes through the daemon (rubber-duck check on
2026-10-18: R/T/U were off by 70/73/10 points that way; only P, pure literals,
balanced). Cell values are kept as OBSERVATION columns (after_value,
before_value) so the gap between "what was credited" and "what Excel shows" is
measurable; it is the rule engine's job (later phase: 0n row-2 weights, 0g +9,
all-colors 111, 1n+ rates) to close it, not this table's.

The DB is host-local (Ix, where the daemon runs) at ~/.local/share/neon/neon.db
(override: $NEON_DB). Never put it in the Syncthing vault. It is fully
rebuildable from the JSONL with `import_jsonl()` (hash-keyed, idempotent), so
the vault JSONL remains the durable record and this is a projection of it —
until the cutover phase flips that direction.

Python 3.9 on Ix (/usr/bin/python3): keep the syntax 3.9-clean.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Iterable, Iterator, Optional

from . import blocks, cols

DB_PATH = Path(os.environ.get("NEON_DB") or Path.home() / ".local/share/neon/neon.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    hash          TEXT NOT NULL UNIQUE,
    ts            TEXT NOT NULL,
    date          TEXT,
    sheet         TEXT NOT NULL,
    col           TEXT NOT NULL,
    row           INTEGER,
    field         TEXT,
    kind          TEXT NOT NULL,
    points        REAL,
    value         TEXT,
    after_value   REAL,
    before_value  REAL,
    after_formula TEXT,
    src           TEXT,
    source        TEXT,
    via           TEXT,
    idem          TEXT,
    chain         TEXT,
    jsonl_kind    TEXT NOT NULL,
    block         TEXT,
    fallback      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_date_sheet_col ON events(date, sheet, col);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS row_dates (
    sheet TEXT NOT NULL,
    row   INTEGER NOT NULL,
    date  TEXT NOT NULL,
    PRIMARY KEY (sheet, row)
);
"""

_LITERAL_RE = re.compile(r"^\s*[+-]?\s*\d+(?:\.\d+)?\s*$")
_MD_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?\s*$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOCK_SRC = "block-turnover lock"
_OBSERVE_KINDS = {"reconcile", "baseline", "excel-edit", "snapshot"}
_SKIP_KINDS = {"ack", "structural"}


# ── connection ───────────────────────────────────────────────────────────────

def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


# ── pure helpers (all unit-tested) ───────────────────────────────────────────

def line_hash(entry: dict) -> str:
    """Identity of one JSONL entry, independent of key order and of whether the
    line came from the daemon's json.dumps or a fallback printf."""
    s = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def parse_ts(ts: str) -> Optional[_dt.datetime]:
    try:
        return _dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None


def resolve_date(raw: Optional[str], ts: _dt.datetime) -> Optional[str]:
    """Sheet date "M/D" → ISO, choosing the year that puts it nearest the
    write timestamp (so a 12/31 write journaled on Jan 1 stays in the old
    year and a 1/1 write journaled on Dec 31 lands in the new one)."""
    if not raw:
        return None
    if _ISO_RE.match(raw):
        return raw
    m = _MD_RE.match(raw)
    if not m:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    if m.group(3):                                  # explicit year: M/D/YY or M/D/YYYY
        y = int(m.group(3))
        y = 2000 + y if y < 100 else y
        try:
            return _dt.date(y, mo, d).isoformat()
        except ValueError:
            return None
    best = None
    for y in (ts.year - 1, ts.year, ts.year + 1):
        try:
            cand = _dt.date(y, mo, d)
        except ValueError:
            continue
        gap = abs((cand - ts.date()).days)
        if best is None or gap < best[0]:
            best = (gap, cand)
    return best[1].isoformat() if best else None


def num(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def field_for(sheet: str, col: str) -> Optional[str]:
    try:
        return cols.header_for(sheet, col)
    except Exception:
        return None


def block_for(ts: _dt.datetime, date_iso: Optional[str]) -> Optional[str]:
    """地支 block of the write, only when the credit lands on the day it was
    written (a backfill to another day has no meaningful block)."""
    if not date_iso or date_iso != ts.date().isoformat():
        return None
    return blocks.current_block(ts)[1]


def classify(entry: dict) -> Optional[dict]:
    """Semantic row for a JSONL entry, or None for kinds the ledger ignores."""
    jk = entry.get("kind") or ""
    if jk in _SKIP_KINDS:
        return None
    value = entry.get("value")
    sval = "" if value is None else str(value)
    after_v = num(entry.get("after_value"))
    before_v = num(entry.get("before_value"))
    src = entry.get("src") or ""

    if jk in _OBSERVE_KINDS:
        kind, points = "observe", None
    elif jk == "append":
        if _LITERAL_RE.match(sval):
            kind = "credit"
            points = float(sval.replace(" ", ""))
        elif sval.lstrip().startswith(("+", "-")):
            kind = "credit_ref"
            points = (round(after_v - before_v, 4)
                      if after_v is not None and before_v is not None else None)
        else:
            kind, points = "text", None
    elif jk == "write":
        if src.startswith(_LOCK_SRC):
            kind, points = "lock", None
        elif sval.startswith("=") or _LITERAL_RE.match(sval) or after_v is not None:
            kind, points = "set", None
        else:
            kind, points = "text", None
    else:
        return None
    return {"kind": kind, "points": points, "after_value": after_v,
            "before_value": before_v}


def to_row(entry: dict, row_date: Optional[str] = None) -> Optional[dict]:
    """Full events-table row for a JSONL entry (None if ignored)."""
    sem = classify(entry)
    if sem is None:
        return None
    ts = parse_ts(entry.get("ts") or "")
    if ts is None:
        return None
    sheet, col = entry.get("sheet") or "", entry.get("col") or ""
    if not sheet or not col:
        return None
    date_iso = resolve_date(entry.get("date"), ts) or row_date
    return {
        "hash": line_hash(entry),
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%S"),
        "date": date_iso,
        "sheet": sheet, "col": col, "row": entry.get("row"),
        "field": field_for(sheet, col),
        "kind": sem["kind"], "points": sem["points"],
        "value": None if entry.get("value") is None else str(entry.get("value")),
        "after_value": sem["after_value"], "before_value": sem["before_value"],
        "after_formula": entry.get("after"),
        "src": entry.get("src"), "source": entry.get("source"),
        "via": entry.get("via"), "idem": entry.get("idem"),
        "chain": entry.get("chain"), "jsonl_kind": entry.get("kind"),
        "block": block_for(ts, date_iso),
        "fallback": 1 if entry.get("fallback") else 0,
    }


# ── writes ───────────────────────────────────────────────────────────────────

_COLS = ("hash", "ts", "date", "sheet", "col", "row", "field", "kind", "points",
         "value", "after_value", "before_value", "after_formula", "src", "source",
         "via", "idem", "chain", "jsonl_kind", "block", "fallback")
_INSERT = (f"INSERT OR IGNORE INTO events ({', '.join(_COLS)}) "
           f"VALUES ({', '.join('?' for _ in _COLS)})")


def _learn_row_date(c: sqlite3.Connection, entry: dict, date_iso: Optional[str]) -> None:
    row = entry.get("row")
    if row is None or not date_iso or not entry.get("date"):
        return
    c.execute("INSERT OR REPLACE INTO row_dates (sheet, row, date) VALUES (?,?,?)",
              (entry.get("sheet"), int(row), date_iso))


def _row_date(c: sqlite3.Connection, sheet: str, row) -> Optional[str]:
    if row is None:
        return None
    r = c.execute("SELECT date FROM row_dates WHERE sheet=? AND row=?",
                  (sheet, int(row))).fetchone()
    return r[0] if r else None


def record(entry: dict, conn: Optional[sqlite3.Connection] = None) -> Optional[int]:
    """Double-write one journaled entry. Returns the new row id, None if the
    entry is ignored or already present. Raises only on DB failure (the
    daemon wraps this so an Excel write that already landed can't 500)."""
    own = conn is None
    c = conn or connect()
    try:
        hint = _row_date(c, entry.get("sheet") or "", entry.get("row"))
        row = to_row(entry, row_date=hint)
        if row is None:
            return None
        if entry.get("date"):
            _learn_row_date(c, entry, row["date"])
        cur = c.execute(_INSERT, tuple(row[k] for k in _COLS))
        if own:
            c.commit()
        return cur.lastrowid if cur.rowcount else None
    finally:
        if own:
            c.close()


def iter_jsonl(path: Path) -> Iterator[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return


def import_jsonl(paths: Iterable[Path], conn: Optional[sqlite3.Connection] = None) -> dict:
    """Rebuild/extend the DB from JSONL files. Two passes per file so a
    row-addressed write (ritual -1n: no date in the request) can borrow the
    date that a date-addressed write to the same row established, even if
    that write came later in the day. Idempotent via the line hash."""
    own = conn is None
    c = conn or connect()
    stats = {"files": 0, "seen": 0, "inserted": 0, "ignored": 0}
    try:
        for p in paths:
            entries = list(iter_jsonl(Path(p)))
            stats["files"] += 1
            for e in entries:
                ts = parse_ts(e.get("ts") or "")
                if ts is not None and e.get("date") and e.get("row") is not None:
                    _learn_row_date(c, e, resolve_date(e.get("date"), ts))
            for e in entries:
                stats["seen"] += 1
                hint = _row_date(c, e.get("sheet") or "", e.get("row"))
                row = to_row(e, row_date=hint)
                if row is None:
                    stats["ignored"] += 1
                    continue
                cur = c.execute(_INSERT, tuple(row[k] for k in _COLS))
                stats["inserted"] += 1 if cur.rowcount else 0
            c.commit()
        return stats
    finally:
        if own:
            c.close()


# ── reads ────────────────────────────────────────────────────────────────────

def day(date_iso: str, sheet: str = "0分", conn: Optional[sqlite3.Connection] = None) -> list:
    """Per-column view of one day: credited points vs the last value Excel
    showed. Each item: {col, field, credits, ref_points, last_set, observed,
    n_events}. `credits` sums literal credits; `ref_points` sums resolved
    cross-sheet references; `last_set` is the newest /write value; `observed`
    is the newest after_value from any event (what the cell read last)."""
    own = conn is None
    c = conn or connect()
    try:
        rows = c.execute(
            """SELECT col, field,
                      COALESCE(SUM(CASE WHEN kind='credit' THEN points END), 0),
                      COALESCE(SUM(CASE WHEN kind='credit_ref' THEN points END), 0),
                      (SELECT after_value FROM events e2
                        WHERE e2.date=e.date AND e2.sheet=e.sheet AND e2.col=e.col
                          AND e2.kind='set' ORDER BY e2.ts DESC, e2.id DESC LIMIT 1),
                      (SELECT after_value FROM events e3
                        WHERE e3.date=e.date AND e3.sheet=e.sheet AND e3.col=e.col
                          AND e3.after_value IS NOT NULL
                        ORDER BY e3.ts DESC, e3.id DESC LIMIT 1),
                      COUNT(*)
                 FROM events e WHERE date=? AND sheet=?
                GROUP BY col, field ORDER BY col""",
            (date_iso, sheet)).fetchall()
        return [{"col": r[0], "field": r[1], "credits": r[2], "ref_points": r[3],
                 "last_set": r[4], "observed": r[5], "n_events": r[6]} for r in rows]
    finally:
        if own:
            c.close()


def _cell_value(kind: str, after_value, raw_value, after_formula) -> tuple:
    """(value, text) a cell is taken to hold after an event. after_value is the
    daemon's read-back; it is "" for three different things (a cleared cell,
    an empty-block lock = 0, and a read-back glitch where the write landed but
    the echo was empty), so: set/credit with no read-back fall back to the
    request value; a lock whose after-formula is "" is a 0 block."""
    if after_value is not None:
        return after_value, None
    if kind == "lock":
        return (0.0, None) if not (after_formula or "").strip() else (None, after_formula)
    if kind in ("set", "credit"):
        v = num(raw_value) if raw_value is not None and not str(raw_value).startswith(("=", "+", "-")) else None
        if v is not None:
            return v, None
    return None, raw_value if raw_value not in (None, "") else ""


def state(date_iso: str, sheet: str, conn: Optional[sqlite3.Connection] = None,
          writes_only: bool = False) -> dict:
    """What the ledger last saw in each cell of `sheet` on `date_iso`:
    {col: {"value": float|None, "text": str|None, "kind": str, "ts": str,
    "field": str|None, "formula": str|None}}. The newest event per column
    wins. A column with no event is ABSENT — callers must treat absence as
    UNKNOWN, not blank (cells marked before the double-write began have no
    event). Row-addressed events that landed before their row's date was
    learned (date NULL) are included via row_dates.

    writes_only=True is the ledger-as-source view: only what writers sent
    through the daemon (plus the 03:50 day-start baseline) counts; Excel-side
    observations (reconcile/snapshot) are ignored, so a hand edit in Excel is
    invisible here by design."""
    own = conn is None
    c = conn or connect()
    obs = ("AND (e2.kind != 'observe' OR e2.jsonl_kind = 'baseline')" if writes_only else "")
    try:
        rows = c.execute(
            f"""SELECT e.col, e.field, e.kind, e.ts, e.after_value, e.value, e.after_formula
                 FROM events e
                 JOIN (SELECT e2.col, MAX(e2.id) AS mid FROM events e2
                        LEFT JOIN row_dates rd ON rd.sheet = e2.sheet AND rd.row = e2.row
                        WHERE e2.sheet=? AND COALESCE(e2.date, rd.date) = ?
                          AND e2.kind IN ('set','observe','text','lock','credit','credit_ref')
                          {obs}
                        GROUP BY e2.col) m ON m.mid = e.id
                ORDER BY e.col""", (sheet, date_iso)).fetchall()
        out = {}
        for col, field, kind, ts, av, raw, formula in rows:
            value, text = _cell_value(kind, av, raw, formula)
            out[col] = {"value": value, "text": text, "kind": kind, "ts": ts,
                        "field": field, "formula": formula}
        return out
    finally:
        if own:
            c.close()


def repair_dates(conn: Optional[sqlite3.Connection] = None) -> int:
    """Fill `date` (and block) on row-addressed events recorded before their
    row's date was learned (e.g. the 06:00 block lock that beat the day's
    first dated write by seconds). Returns rows repaired."""
    own = conn is None
    c = conn or connect()
    try:
        rows = c.execute(
            """SELECT e.id, e.ts, rd.date FROM events e
                 JOIN row_dates rd ON rd.sheet = e.sheet AND rd.row = e.row
                WHERE e.date IS NULL AND e.sheet IN ('0分', '0n', 'hcbi')""").fetchall()
        for rid, ts, d in rows:
            t = parse_ts(ts)
            c.execute("UPDATE events SET date=?, block=? WHERE id=?",
                      (d, block_for(t, d) if t else None, rid))
        c.commit()
        return len(rows)
    finally:
        if own:
            c.close()


def credits(date_iso: str, sheet: str = "0分", conn: Optional[sqlite3.Connection] = None) -> dict:
    """Σ literal credits and Σ resolved reference credits per column for one
    day: {col: {"credit": float, "credit_ref": float, "ref_unknown": int}}
    where ref_unknown counts credit_ref events whose points could not be
    resolved (fallback entries with no cell values)."""
    own = conn is None
    c = conn or connect()
    try:
        rows = c.execute(
            """SELECT col,
                      COALESCE(SUM(CASE WHEN kind='credit' THEN points END), 0),
                      COALESCE(SUM(CASE WHEN kind='credit_ref' THEN points END), 0),
                      SUM(CASE WHEN kind='credit_ref' AND points IS NULL THEN 1 ELSE 0 END)
                 FROM events WHERE date=? AND sheet=? GROUP BY col""", (date_iso, sheet)).fetchall()
        return {r[0]: {"credit": r[1], "credit_ref": r[2], "ref_unknown": r[3]} for r in rows}
    finally:
        if own:
            c.close()


def recent(n: int = 20, conn: Optional[sqlite3.Connection] = None) -> list:
    own = conn is None
    c = conn or connect()
    try:
        return c.execute(
            "SELECT ts, date, sheet, col, field, kind, points, after_value, src, block "
            "FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    finally:
        if own:
            c.close()


def status(conn: Optional[sqlite3.Connection] = None) -> dict:
    own = conn is None
    c = conn or connect()
    try:
        n, first, last = c.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM events").fetchone()
        kinds = dict(c.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall())
        return {"db": str(DB_PATH), "events": n, "first": first, "last": last, "kinds": kinds}
    finally:
        if own:
            c.close()
