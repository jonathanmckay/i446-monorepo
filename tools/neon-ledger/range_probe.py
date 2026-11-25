#!/usr/bin/env python3
"""range_probe: would the Neon book answer /read_range the way Excel does?

Reads have no journal, so backend_shadow cannot judge them. This probe asks
the daemon (Excel) for the ranges the real callers read, builds the live book
(lib/neon/book.load: seed + journaled writes), asks Excel again, and compares
cell by cell with bookops.read_range. A range whose two Excel reads differ was
changing under the probe and is not judged.

A separate process on purpose, like backend_shadow: no hook in the daemon.
Read-only towards Excel, the daemon and the ledger. One line per run in
~/.cache/neon-rules/range-probe.jsonl.

  range_probe.py run        probe every range, print and log the verdict
  range_probe.py summary    totals by day from the log

Per cell: exact | format (same number, other text) | differ. A `differ` on a
cell whose content the book and Excel disagree about is `drift` (typed into
Excel or written by a raw AppleScript since the seed; `/read` tells) and does
not count against the renderer. Exit 0 clean, 1 a judged difference, 2 no
book or no daemon. Python 3.9-clean (runs on Ix).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from neon import book, bookops, calc, excel  # noqa: E402

LOG = Path(os.environ.get("NEON_RANGE_PROBE_LOG") or Path.home() / ".cache/neon-rules/range-probe.jsonl")
NUM_TOL = 1e-9
MAX_EXAMPLES = 8
MAX_DRIFT_CHECKS = 12


def ranges(b: calc.Book, today: dt.date) -> list:
    """(caller, sheet, range) for every /read_range a tool issues. Rows that
    depend on the day are resolved in the book, as the callers resolve them
    through /lookup."""
    out = [
        ("week_fill", "1n+", "B5:AL57"),
        ("2n1", "1n+", "A1:AP60"),
        ("monthly", "1n+", "A61:P90"),
        ("task-checksum", "hcbi", "B3:R400"),
        ("janus-mobile", "0n", "D1:AS1"),
        ("janus-mobile", "0n", "AQ375:AS375"),
        ("janus-mobile", "hcbi", "E384:X388"),
        ("allcolors", "1n+", "C1:AL3"),
        ("allcolors", "1n+", "B1:B80"),
    ]
    r = book.row_for_date(b, "0分", today)
    if r:
        out.append(("shadow_apply", "0分", f"D{r}:Y{r}"))
    r = book.row_for_date(b, "0n", today)
    if r:
        out.append(("janus-mobile", "0n", f"D{r}:AS{r}"))
        out.append(("allcolors", "0n", f"C{max(2, r - 14)}:AS{r}"))
    r = book.row_for_date(b, "hcbi", today)
    if r:
        out.append(("1hcb", "hcbi", f"B{max(3, r - 7)}:AA{max(400, r + 30)}"))
    return out


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def same(mine: str, theirs: str) -> str:
    """"exact" | "format" | "" (different)."""
    if mine == theirs:
        return "exact"
    x, y = _num(mine), _num(theirs)
    if x is not None and y is not None and abs(x - y) <= NUM_TOL * max(1.0, abs(y)):
        return "format"
    return ""


def compare(mine: list, theirs: list, sheet: str, rng: str) -> dict:
    m = bookops._RANGE.match(rng.upper())
    c1, r1 = calc.col_index(m.group(1)), int(m.group(2))
    res = {"cells": 0, "exact": 0, "format": 0, "differ": 0, "examples": [], "shape": None}
    if len(mine) != len(theirs) or any(len(a) != len(b) for a, b in zip(mine, theirs)):
        res["shape"] = "book %dx%d, excel %dx%d" % (len(mine), len(mine[0]) if mine else 0,
                                                    len(theirs), len(theirs[0]) if theirs else 0)
        return res
    for i, (ra, rb) in enumerate(zip(mine, theirs)):
        for j, (a, b) in enumerate(zip(ra, rb)):
            res["cells"] += 1
            s = same(a, b)
            res[s or "differ"] += 1
            if s != "exact" and len(res["examples"]) < MAX_EXAMPLES:
                res["examples"].append({"cell": "%s%d" % (calc.col_letter(c1 + j), r1 + i), "kind": s or "differ",
                                        "book": a[:60], "excel": b[:60]})
    return res


def _drift(b: calc.Book, sheet: str, examples: list) -> int:
    """How many differing example cells hold different CONTENT in Excel than
    in the book (so the range reply could not have matched)."""
    n = 0
    for ex in examples[:MAX_DRIFT_CHECKS]:
        if ex["kind"] != "differ":
            continue
        m = bookops.re.match(r"^([A-Z]+)(\d+)$", ex["cell"])
        r = excel.read(sheet, m.group(1), row=int(m.group(2)))
        if not r.get("ok"):
            continue
        _, mine = bookops.read(b, sheet, m.group(1), int(m.group(2)))
        if (r.get("formula") or "") != mine:
            ex["kind"] = "drift"
            ex["excel_formula"], ex["book_formula"] = (r.get("formula") or "")[:60], mine[:60]
            n += 1
    return n


def run(today: dt.date = None, log: Path = None) -> dict:
    today = today or dt.date.today()
    b0 = book.load(today=today)
    if b0 is None:
        return {"error": "no book seed"}
    todo = ranges(b0, today)
    first = {}
    for caller, sheet, rng in todo:
        r = excel.read_range(sheet, rng)
        first[(sheet, rng)] = r.get("rows") if r.get("ok") else None
    if all(v is None for v in first.values()):
        return {"error": "daemon gave no range"}
    b = book.load(today=today)                    # after the first pass: every write Excel showed is journaled
    out = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "seed_saved": b.meta.get("saved"),
           "through": b.through, "dates_known": b.dates is not None, "ranges": [],
           "cells": 0, "exact": 0, "format": 0, "differ": 0, "drift": 0, "unstable": 0, "failed": 0}
    for caller, sheet, rng in todo:
        row = {"caller": caller, "sheet": sheet, "range": rng}
        r = excel.read_range(sheet, rng)
        second = r.get("rows") if r.get("ok") else None
        if first[(sheet, rng)] is None or second is None:
            row["state"] = "failed"
            out["failed"] += 1
        elif first[(sheet, rng)] != second:
            row["state"] = "unstable"
            out["unstable"] += 1
        else:
            c = compare(bookops.read_range(b, sheet, rng), second, sheet, rng)
            if c["shape"]:
                row.update(state="shape", shape=c["shape"])
                out["differ"] += 1
            else:
                drift = _drift(b, sheet, c["examples"]) if c["differ"] else 0
                row.update(state="clean" if not (c["differ"] or c["format"]) else "diff",
                           cells=c["cells"], exact=c["exact"], format=c["format"], differ=c["differ"],
                           drift_examples=drift)
                if c["examples"]:
                    row["examples"] = c["examples"]
                for k in ("cells", "exact", "format", "differ"):
                    out[k] += c[k]
                out["drift"] += drift
        out["ranges"].append(row)
    # Clean: every judged cell byte-identical, apart from cells whose content
    # drifted (those are counted, and are the hand-edit problem, not this one).
    judged_bad = sum(1 for r in out["ranges"] for ex in r.get("examples", []) if ex["kind"] in ("differ", "format"))
    out["clean"] = judged_bad == 0 and not any(r["state"] == "shape" for r in out["ranges"])
    try:
        log = log or LOG
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return out


def summary(log: Path = None) -> dict:
    days: dict = {}
    try:
        with open(log or LOG) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                d = days.setdefault(e["ts"][:10], {"runs": 0, "clean": 0, "cells": 0, "differ": 0, "format": 0,
                                                   "drift": 0, "unstable": 0})
                d["runs"] += 1
                d["clean"] += 1 if e.get("clean") else 0
                for k in ("cells", "differ", "format", "drift", "unstable"):
                    d[k] += e.get(k) or 0
    except OSError:
        return {"error": "no log"}
    return {"days": days}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("-v", action="store_true", help="print every range, not only the differing ones")
    a = ap.parse_args(argv)
    if a.cmd == "summary":
        print(json.dumps(summary(), ensure_ascii=False, indent=1))
        return 0
    out = run()
    if "error" in out:
        print(json.dumps(out))
        return 2
    shown = dict(out)
    if not a.v:
        shown["ranges"] = [r for r in out["ranges"] if r["state"] != "clean"]
    print(json.dumps(shown, ensure_ascii=False, indent=1))
    return 0 if out["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
