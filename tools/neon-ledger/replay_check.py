#!/usr/bin/env python3
"""replay_check — dry-run of the ledger-first block job (Neon off Excel, step 4b).

Decision (JM, 2026-11-19): the block job REPLAYS queued write requests into
Excel (habit marks, +N appends, sets) and lets Excel recalculate; it does not
overwrite 0分 with derived totals, because Excel stays the independent
checksum. So the question before any flip is: given only what a writer asked
for, can the request be replayed to the cell content the daemon actually
produced?

The vault journal answers that without touching Excel. Every daemon write is
journaled with the request (`kind`, `value`) and the cell's formula before and
after. For each request this folds `apply(before, kind, value)` and compares it
with the journaled `after`:

  reproduced  the replay yields exactly what Excel ended up holding
  needs_mode  a /batch_write mode "add" (cumulative habit): after = before + N.
              The journal records it as a plain write with no mode, so it can
              only be replayed once the daemon journals `mode`
  lock        build-order-daemon's G:O freeze: the block job's own step, not a
              queued request
  no_effect   the daemon journaled the request but the cell did not change
              (after = before): the write never landed in Excel. A replay
              would hit the same wall, so these are bugs in their own right
  mismatch    the replay would have produced something else: a replay bug or a
              write path this model does not know

and, independently of replay semantics:

  chain_break the cell did not hold what the previous journal entry left there
              (an edit typed into Excel between two writes). After the flip
              Excel is fed only by the block job, so each of these is an edit
              that must arrive as a request instead.

Read-only: reads vault/g245/neon-ledger/*.jsonl, writes nothing but an optional
JSON report.

  replay_check.py [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--json PATH] [--show N]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

LEDGER_DIR = Path(os.environ.get("NEON_LEDGER_DIR", Path.home() / "vault" / "g245" / "neon-ledger"))
LOCK_SRC = "block-turnover lock"
REQUEST_KINDS = {"write", "append"}
# Entries that only record what Excel holds; they move the cell's known content.
OBSERVE_KINDS = {"baseline", "reconcile", "excel-edit", "snapshot", "ack"}
_NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_POINT_ZERO = re.compile(r"(?<![\d.])(\d+)\.0+(?![\d])")   # Excel stores "+15.0" as "+15"


def _s(v) -> str:
    return "" if v is None else str(v)


def _num(s: str):
    s = s.strip()
    return float(s) if _NUM.match(s) else None


def same(a, b) -> bool:
    """Cell contents equal: exact after trimming, or numerically ("1" ≡ "1.0")."""
    a, b = _s(a).strip(), _s(b).strip()
    if a == b:
        return True
    if a.startswith("=") and b.startswith("=") and _POINT_ZERO.sub(r"\1", a) == _POINT_ZERO.sub(r"\1", b):
        return True
    x, y = _num(a), _num(b)
    return x is not None and y is not None and abs(x - y) < 1e-9


def apply(before: str, kind: str, value) -> str:
    """What the daemon's setter leaves in a cell holding `before` (its formula)
    for a request (kind, value): mirrors services/excel-http/server.py
    do_write/_as_setter and _append_setters. Mode "add" is not modelled here on
    purpose: the journal does not say which writes were adds."""
    before = _s(before)
    val = _s(value)
    if kind == "write":
        return val
    if kind != "append":
        raise ValueError(kind)
    numeric = val.lstrip().startswith(("+", "=", "-"))
    if before in ("", "0"):
        return "=" + val.lstrip("+") if numeric else val.lstrip(", ")
    if numeric and not before.startswith("="):
        return "=" + before + val
    return before + val


def is_add(before, value, after) -> bool:
    """A numeric write whose result is old + N rather than N: mode "add"."""
    b, v, a = _num(_s(before)), _num(_s(value)), _num(_s(after))
    if v is None or a is None or b in (None, 0.0):
        return False
    return abs((b + v) - a) < 1e-9 and abs(v - a) > 1e-9


def classify(entry: dict) -> str:
    """One journaled request → reproduced | needs_mode | lock | no_effect | mismatch."""
    if _s(entry.get("src")).startswith(LOCK_SRC):
        return "lock"
    before, after = entry.get("before"), entry.get("after")
    try:
        predicted = apply(before, entry.get("kind"), entry.get("value"))
    except ValueError:
        return "mismatch"
    if same(predicted, after):
        return "reproduced"
    if entry.get("mode") == "add":             # journaled since excel-http 1.12.1
        return "reproduced" if is_add(before, entry.get("value"), after) or same(before, after) else "mismatch"
    if entry.get("kind") == "write" and is_add(before, entry.get("value"), after):
        return "needs_mode"
    if same(before, after):
        return "no_effect"
    return "mismatch"


def iter_entries(start: _dt.date, end: _dt.date):
    months = sorted({(d.year, d.month) for d in (start + _dt.timedelta(n) for n in range((end - start).days + 1))})
    for y, m in months:
        p = LEDGER_DIR / f"{y:04d}-{m:02d}.jsonl"
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                day = _s(e.get("ts"))[:10]
                if start.isoformat() <= day <= end.isoformat():
                    yield e


def check(entries) -> dict:
    """Fold the journal in order. → {"days": {date: Counter}, "by_sheet":
    {sheet: Counter}, "mismatches": [...], "chain_breaks": [...]}."""
    known: dict = {}                       # (sheet, row, col) → last journaled content
    days: dict = defaultdict(Counter)
    by_sheet: dict = defaultdict(Counter)
    mismatches, breaks = [], []
    for e in entries:
        kind = e.get("kind")
        key = (e.get("sheet"), e.get("row"), e.get("col"))
        day = _s(e.get("ts"))[:10]
        if kind in REQUEST_KINDS and not e.get("fallback"):
            verdict = classify(e)
            days[day][verdict] += 1
            days[day]["requests"] += 1
            by_sheet[e.get("sheet")][verdict] += 1
            brief = {k: e.get(k) for k in ("ts", "sheet", "col", "row", "kind", "value", "before", "after", "src", "via")}
            if verdict in ("mismatch", "no_effect", "needs_mode"):
                brief["verdict"] = verdict
                brief["predicted"] = apply(e.get("before"), kind, e.get("value"))
                mismatches.append(brief)
            if key in known and not same(known[key], e.get("before")) and verdict != "lock":
                days[day]["chain_break"] += 1
                breaks.append({**brief, "expected_before": known[key]})
        elif kind in REQUEST_KINDS:        # daemon-down fallback line: no read-back to judge
            days[day]["fallback"] += 1
        if kind in REQUEST_KINDS or kind in OBSERVE_KINDS:
            if e.get("after") is not None:
                known[key] = e.get("after")
    return {"days": {d: dict(c) for d, c in sorted(days.items())},
            "by_sheet": {s: dict(c) for s, c in sorted(by_sheet.items(), key=lambda kv: _s(kv[0]))},
            "mismatches": mismatches, "chain_breaks": breaks}


def main() -> int:
    p = argparse.ArgumentParser(prog="replay_check")
    today = _dt.date.today()
    p.add_argument("--from", dest="start", default=(today - _dt.timedelta(days=13)).isoformat())
    p.add_argument("--to", dest="end", default=today.isoformat())
    p.add_argument("--json")
    p.add_argument("--show", type=int, default=12)
    a = p.parse_args()
    start, end = _dt.date.fromisoformat(a.start), _dt.date.fromisoformat(a.end)
    # Fold from the first of the month so a cell's known content exists before the window.
    res = check(iter_entries(start.replace(day=1), end))
    res["days"] = {d: c for d, c in res["days"].items() if a.start <= d <= a.end}
    res["mismatches"] = [m for m in res["mismatches"] if a.start <= m["ts"][:10] <= a.end]
    res["chain_breaks"] = [m for m in res["chain_breaks"] if a.start <= m["ts"][:10] <= a.end]
    cols = ["requests", "reproduced", "needs_mode", "lock", "no_effect", "mismatch", "chain_break", "fallback"]
    print("date        " + " ".join(f"{c:>11}" for c in cols))
    tot = Counter()
    for d, c in res["days"].items():
        tot.update(c)
        print(f"{d}  " + " ".join(f"{c.get(k, 0):>11}" for k in cols))
    print("total       " + " ".join(f"{tot.get(k, 0):>11}" for k in cols))
    for title, rows in (("not reproduced", res["mismatches"]), ("chain breaks", res["chain_breaks"])):
        print(f"\n{title}: {len(rows)}")
        for m in rows[: a.show]:
            print("  " + json.dumps(m, ensure_ascii=False)[:300])
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
