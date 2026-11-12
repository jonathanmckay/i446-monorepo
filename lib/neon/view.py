"""Neon day view from the ledger alone (Neon off Excel, step 4a — 2026-11-11).

What a reader (dtd /api/summary, janus mobile) shows *alongside* Excel until
the shadow-apply log proves the intraday derivation: Σ and the P..Y point
columns from `rules.derive_day(strict=True)`, per-block 分 from the lock
snapshots the build-order daemon wrote (never from event timestamps — the
ledger's event attribution undercounts P sets and late credits), and the
live block as the residual D − Σlocks, exactly how Excel's G:O formulas
measure it.

Only `derived` is read. `status`/`observed`/`delta` are meaningless intraday:
Excel is observed at 03:50 and 23:55, so mid-day every flow column reports
"mismatch" against a stale 0 (rubber-duck, 2026-11-11).

Never raises. Returns None when the ledger DB does not exist on this host
(the DB lives on Ix only; `ledger.connect()` would otherwise create an empty
one) or the derivation fails, so callers degrade to Excel.

Python 3.9-clean (runs on Ix).
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Optional

from neon import blocks as _blocks
from neon import ledger, rules

READ_SOURCE_ENV = "NEON_READ_SOURCE"          # "excel" (default) | "ledger"
BLOCK_ORDER = ["卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]


def read_source() -> str:
    v = (os.environ.get(READ_SOURCE_ENV) or "excel").strip().lower()
    return "ledger" if v == "ledger" else "excel"


def db_present() -> bool:
    return ledger.DB_PATH.exists()


def _round(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(float(v), 3)


def day_view(date_iso: str, conn=None, now: Optional[_dt.datetime] = None) -> Optional[dict]:
    """{"date", "total", "cols": {col: 分}, "blocks": {glyph: 分}, "live_block",
    "unknown": [...], "pending": [...], "through": ts|None, "source": "ledger"}.

    blocks: every locked column at its lock value; the current block (today
    only) at max(0, total − Σlocks); future blocks absent.
    unknown: inputs the ledger never observed (derivation is partial).
    pending: columns with no derivation yet (P before its first ritual set).
    """
    if conn is None and not db_present():
        return None
    # Tests never read the live ledger (same contract as neon.excel's
    # transport guard): without an injected conn, pytest gets None unless
    # NEON_TEST_DB_OK=1.
    if conn is None and os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("NEON_TEST_DB_OK"):
        return None
    own = conn is None
    try:
        c = conn or ledger.connect()
    except Exception:  # noqa: BLE001 — a reader must never fail on the ledger
        return None
    try:
        try:
            day = rules.derive_day(date_iso, c, strict=True)
        except Exception:  # noqa: BLE001
            return None
        cols, unknown, pending = {}, [], []
        for col in rules.POINT_COLS:
            r = day["cols"].get(col) or {}
            d = r.get("derived")
            if d is None:
                pending.append(col)
            else:
                cols[col] = _round(d)
            unknown += r.get("unknown") or []
        total = day["cols"].get("D", {}).get("derived")
        if total is None:
            total = sum(cols.values()) if cols else None
        total = _round(total)

        locks = {}
        for col, glyph in rules.BLOCK_COLS.items():
            b = day["blocks"].get(glyph) or {}
            if b.get("lock") is not None:
                locks[glyph] = _round(b["lock"])
        out_blocks = dict(locks)
        live = None
        today = (now or _dt.datetime.now()).date().isoformat()
        if date_iso == today and total is not None:
            _, live = _blocks.current_block(now)
            if live in BLOCK_ORDER and live not in locks:
                resid = max(0.0, total - sum(locks.values()))
                out_blocks[live] = 0.0 if resid < 0.01 else _round(resid)   # float dust → 0
            elif live not in BLOCK_ORDER:
                live = None

        row = c.execute("SELECT MAX(ts) FROM events WHERE date=?", (date_iso,)).fetchone()
        through = row[0] if row else None
        return {"date": date_iso, "total": total, "cols": cols, "blocks": out_blocks,
                "live_block": live, "unknown": sorted(set(unknown)), "pending": pending,
                "through": through, "source": "ledger"}
    finally:
        if own:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass


def pick(excel_value, ledger_value):
    """The value a reader shows, by NEON_READ_SOURCE. Excel wins by default;
    with "ledger" the ledger value is shown when it exists, else Excel."""
    if read_source() == "ledger" and ledger_value is not None:
        return ledger_value
    return excel_value


READER_DIFF_LOG = os.path.expanduser("~/.cache/neon-rules/reader-diff.jsonl")
_last_logged: dict = {}


def log_reader_diff(reader: str, date_iso: str, excel_value, ledger_value) -> bool:
    """Journal a reader's Excel-vs-ledger disagreement, once per distinct
    (reader, date, excel, ledger) — dtd recomputes every 45 s, so logging
    every call would be a line a minute. Outside the vault on purpose
    (autopush). Returns True when a line was written."""
    if excel_value is None or ledger_value is None:
        return False
    try:
        if abs(float(excel_value) - float(ledger_value)) <= rules.TOL:
            return False
    except (TypeError, ValueError):
        return False
    key = (reader, date_iso, excel_value, ledger_value)
    if _last_logged.get(reader) == key:
        return False
    _last_logged[reader] = key
    try:
        import json
        os.makedirs(os.path.dirname(READER_DIFF_LOG), exist_ok=True)
        with open(READER_DIFF_LOG, "a") as f:
            f.write(json.dumps({"ts": _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                                "reader": reader, "date": date_iso, "excel": excel_value,
                                "ledger": ledger_value}, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True
