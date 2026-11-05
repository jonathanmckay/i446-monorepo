"""Adopt hand-typed Excel edits into the Neon ledger (source=hand).

Neon off Excel, step 3 gap (Todoist 6hp23H; loose-threads v177 T4): over
10-29..31, 96% of the 分 the strict ledger missed came from cells typed
straight into Excel (hcbi food bands, caf, protein …). The 23:55 / 03:50
reconcile (scripts/neon-reconcile.py → excel-http /reconcile) already SEES
those edits and journals them as kind `reconcile` / `excel-edit`, but those
are observations, and the strict view (rules.strict_check, ledger.state
writes_only=True) ignores observations by design. So the ledger alone can
never reproduce 0分!W, whose hcbi!AA composite reads those cells.

This pass closes the gap without touching Excel. It replays the JSONL chain
per hcbi cell (see candidates()) and journals a kind `adopt` entry (source
"hand", via "neon-adopt-hand") for each edit no writer sent: an Excel-side
observation that disagrees with the ledger, or an append whose `before`
shows an edit typed between two writes. `ledger.classify` treats `adopt`
like a daemon `write` (set / text, never points), so strict mode sees it.

Excel remains the source of truth (plan L9): nothing here writes Excel, and
the adopt entry copies Excel's formula verbatim into `value`. Its `before` /
`after` are null on purpose: the daemon's chain index, latest_entry() (what
/reconcile compares against) and the audit replay key on `after`, and an
adoption recorded after the fact (its ts sits in the past, the line at the
end of the file) must not move any of them. The ledger's own pre-image is in
`ledger_before`, the observation or append it came from in `adopted_from`.

Idempotent: one adoption per source line (idem "hand:<line-hash16>", checked
against the DB and the JSONL months), and the replay folds adopt lines back
into the chain, so a rerun finds nothing to do.

Snapshots are not adopted: a snapshot is a value change under an unchanged
formula (a formula cell recalculating), not an edit. Baselines are already
strict-visible (the day-start seed).

Caveat: a writer that still bypasses the daemon (raw ix-osa AppleScript,
daemon-down fallback without a journal line) produces the same reconcile
observation and is adopted as `hand` too. The `adopted_from` hash links
back to the observation, so it can be re-attributed later.

Python 3.9-clean (runs on Ix with /usr/bin/python3).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from . import ledger

LEDGER_DIR = Path(os.environ.get("NEON_LEDGER_DIR") or Path.home() / "vault/g245/neon-ledger")
DEFAULT_SHEETS = ("hcbi",)
EXCEL_KINDS = ("reconcile", "excel-edit")       # JSONL kinds that are Excel-side edits
SRC, SOURCE, VIA = "hand-adopt", "hand", "neon-adopt-hand"


# ── pure helpers ─────────────────────────────────────────────────────────────

def norm(content) -> Optional[str]:
    """Comparable form of a cell's formula/content: blank → None, whitespace
    dropped, numerics canonical ("0" == "0.0" == "=0" is NOT collapsed: a
    formula and a literal differ in Excel and the rule engine honours both)."""
    if content is None:
        return None
    s = str(content).strip()
    if not s:
        return None
    n = ledger.num(s)
    if n is not None:
        return repr(float(n))
    return "".join(s.split())


def sheet_date(date_iso: str) -> str:
    """ISO → the "M/D" form the daemon and reconcile journal with."""
    d = _dt.date.fromisoformat(date_iso)
    return f"{d.month}/{d.day}"


def idem_for(obs_hash: str) -> str:
    return "hand:" + obs_hash[:16]


# ── diff (chain replay over the JSONL) ───────────────────────────────────────
#
# Per cell, `known` = the formula the ledger's own writes imply. Two ways a
# hand edit shows up:
#   (a) an Excel-side observation (reconcile / excel-edit) whose `after`
#       differs from `known`: adopt it at the observation's ts;
#   (b) a daemon append (credit / credit_ref) whose `before` differs from
#       `known`: the edit happened between two writes, so reconcile never saw
#       it (its chain already held the append's after). Strict mode adds the
#       append's points to a stale seed, so adopt the `before` one second
#       BEFORE the append; the credit then lands on the hand-typed base.
# A plain write (set) replaces the cell in Excel too, so an edit it
# overwrote is moot; text appends carry the whole string in `after`.

def _ts_minus(ts: str, seconds: int = 1) -> str:
    t = ledger.parse_ts(ts)
    return (t - _dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S") if t else ts


def _entry_date(e: dict, row_dates: dict) -> Optional[str]:
    ts = ledger.parse_ts(e.get("ts") or "")
    if ts is None:
        return None
    d = ledger.resolve_date(e.get("date"), ts)
    if d is None and e.get("row") is not None:
        d = row_dates.get((e.get("sheet"), e.get("row")))
    return d


def candidates(entries: list, dates: Iterable[str], sheets: Iterable[str] = DEFAULT_SHEETS) -> list:
    """Pending adoptions for `dates` × `sheets` from JSONL entries (any
    order; sorted by ts, file order breaking ties). Each item: {sheet, col,
    row, date, ts, content, ledger_before, from_hash, reason}."""
    dates, sheets = set(dates), set(sheets)
    row_dates: dict = {}
    for e in entries:
        ts = ledger.parse_ts(e.get("ts") or "")
        if ts is not None and e.get("date") and e.get("row") is not None:
            d = ledger.resolve_date(e.get("date"), ts)
            if d:
                row_dates[(e.get("sheet"), e.get("row"))] = d
    order = sorted(range(len(entries)), key=lambda i: (entries[i].get("ts") or "", i))
    known: dict = {}
    out = []
    for i in order:
        e = entries[i]
        if e.get("sheet") not in sheets:
            continue
        d = _entry_date(e, row_dates)
        if d not in dates:
            continue
        key = (e.get("sheet"), e.get("col"), e.get("row"))
        jk = e.get("kind") or ""
        after = e.get("after")
        base = {"sheet": key[0], "col": key[1], "row": key[2], "date": d,
                "from_hash": ledger.line_hash(e)}
        if jk == "baseline":
            known[key] = after
        elif jk == "adopt":
            known[key] = e.get("value")
        elif jk in EXCEL_KINDS:
            if after is None:              # late macro report: no formula to adopt
                continue
            if norm(after) != norm(known.get(key)):
                out.append(dict(base, ts=e["ts"], content=after, after_value=e.get("after_value"),
                                ledger_before=known.get(key), reason=jk))
            known[key] = after
        elif jk in ("write", "append"):
            sem = ledger.classify(e) or {}
            before = e.get("before")
            if (sem.get("kind") in ("credit", "credit_ref") and before is not None
                    and norm(before) != norm(known.get(key))):
                out.append(dict(base, ts=_ts_minus(e["ts"]), content=before,
                                after_value=e.get("before_value"),
                                ledger_before=known.get(key), reason="append-before"))
            if after is not None:
                known[key] = after
    return out


def build_entry(cand: dict, now: _dt.datetime) -> dict:
    content, av = cand["content"], cand.get("after_value")
    av = None if av is None else str(av)
    return {
        "ts": cand["ts"],
        "kind": "adopt", "sheet": cand["sheet"], "col": cand["col"], "row": cand["row"],
        "date": sheet_date(cand["date"]),
        "value": content, "after_value": av,
        "before": None, "after": None,      # chain-invisible, see module doc
        "ledger_before": cand["ledger_before"],
        "src": SRC, "source": SOURCE, "via": VIA,
        "idem": idem_for(cand["from_hash"]), "adopted_from": cand["from_hash"],
        "reason": cand["reason"], "adopted_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ── idempotency + IO ─────────────────────────────────────────────────────────

def _month_files(ledger_dir: Path, now: _dt.datetime) -> list:
    prev = now.replace(day=1) - _dt.timedelta(days=1)
    return [ledger_dir / f"{m.strftime('%Y-%m')}.jsonl" for m in (prev, now)]


def _append_line(path: Path, entry: dict) -> None:
    """One write(2) on an O_APPEND fd: the daemon appends to the same file
    from another process, and a single short append cannot interleave."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def load_entries(ledger_dir: Path, now: _dt.datetime) -> list:
    out = []
    for p in _month_files(ledger_dir, now):
        out.extend(ledger.iter_jsonl(p))
    return out


def run(dates: Iterable[str], sheets: Iterable[str] = DEFAULT_SHEETS,
        conn: Optional[sqlite3.Connection] = None, ledger_dir: Optional[Path] = None,
        now: Optional[_dt.datetime] = None, dry_run: bool = False) -> list:
    """Adopt every pending hand edit for `dates` × `sheets`: append the adopt
    entries to the current month's JSONL (the durable record) and record them
    in the DB. Returns the entries written (or that would be, with dry_run)."""
    own = conn is None
    c = conn or ledger.connect()
    ldir = Path(ledger_dir or LEDGER_DIR)
    now = now or _dt.datetime.now()
    try:
        entries = load_entries(ldir, now)
        seen = {r[0] for r in c.execute("SELECT idem FROM events WHERE idem LIKE 'hand:%'")}
        seen |= {e["idem"] for e in entries if e.get("kind") == "adopt" and e.get("idem")}
        out = []
        for cand in candidates(entries, list(dates), sheets):
            entry = build_entry(cand, now)
            if entry["idem"] in seen:
                continue
            seen.add(entry["idem"])
            out.append(entry)
            if dry_run:
                continue
            _append_line(ldir / f"{now.strftime('%Y-%m')}.jsonl", entry)
            ledger.record(entry, c)
        if not dry_run:
            c.commit()
        return out
    finally:
        if own:
            c.close()
