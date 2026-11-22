#!/usr/bin/env python3
"""book_check: is "seed + ledger writes, computed by neon.calc" the workbook?

  book_check.py seed      snapshot the saved workbook into the seed (run once,
                          then whenever a check passes or a hand edit is found)
  book_check.py check     replay the ledger's writes on the seed and compare,
                          cell by cell, with the workbook as saved NOW: every
                          cell's content and every formula's value. A content
                          difference is a write the ledger never saw (typed
                          into Excel) or one it recorded wrongly.
        --reseed          then move the seed up to this save, clean or not
                          (a difference is logged first: it is a hand edit)
  book_check.py status    the seed's age and what the replay would apply

Read-only towards Excel, the daemon and the ledger. Exit 0 clean, 1 differs,
2 no seed / no workbook. Appends one line per check to
~/.cache/neon-rules/book-check.jsonl.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from neon import book, calc  # noqa: E402

LOG = Path.home() / ".cache/neon-rules/book-check.jsonl"
TOL = 1e-6


def _same_value(got, want) -> bool:
    if isinstance(want, calc.Err) or isinstance(got, calc.Err):
        return want == got
    if isinstance(want, bool) or isinstance(got, bool):
        return want is got
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(got - want) <= TOL * max(1.0, abs(want))
    if want in (None, "") and got in (None, "", 0):
        return True
    return got == want


def _same_content(x, y) -> bool:
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) <= 1e-9 * max(1.0, abs(y))
    return x == y


def _cell(k) -> str:
    return f"{k[0]}!{calc.col_letter(k[1])}{k[2]}"


def check(src: Path, seed_path: Path, limit: int = 20) -> dict:
    live = book.load(seed_path)
    if live is None:
        return {"error": "no seed; run: book_check.py seed"}
    tmp = Path(tempfile.mkdtemp(prefix="book-check-"))
    try:
        copy = tmp / "neon.xlsx"
        shutil.copy2(src, copy)
        mtime = os.path.getmtime(src)
        now_book, cached = calc.load_xlsx(copy, today=dt.date.fromtimestamp(mtime))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    saved = dt.datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
    # the workbook's save may trail the ledger: compare the book as of that save
    upto = (dt.datetime.fromtimestamp(mtime) - dt.timedelta(seconds=5)).isoformat(timespec="seconds")
    at_save = book.load(seed_path, upto=upto, today=dt.date.fromtimestamp(mtime))
    content = [(k, at_save.cells.get(k), now_book.cells.get(k))
               for k in set(at_save.cells) | set(now_book.cells)
               if not _same_content(at_save.cells.get(k), now_book.cells.get(k))]
    values, unsupported = [], 0
    for k, want in cached.items():
        try:
            got = at_save.value(*k)
        except calc.CalcError:
            unsupported += 1
            continue
        if not _same_value(got, want):
            values.append((k, got, want))
    out = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "seed_saved": live.meta["saved"],
           "workbook_saved": saved, "writes_applied": at_save.applied, "writes_after_save": live.applied - at_save.applied,
           "unknown_writes": len(at_save.unknown), "cells": len(now_book.cells), "formulas": len(cached),
           "content_differ": len(content), "value_differ": len(values), "unsupported": unsupported,
           "content_sample": [{"cell": _cell(k), "book": repr(x)[:80], "excel": repr(y)[:80]}
                              for k, x, y in sorted(content, key=lambda t: str(t[0]))[:limit]],
           "value_sample": [{"cell": _cell(k), "book": repr(g)[:40], "excel": repr(w)[:40],
                             "formula": str(at_save.content(*k))[:100]}
                            for k, g, w in sorted(values, key=lambda t: str(t[0]))[:limit]]}
    out["clean"] = not (content or values or unsupported or at_save.unknown)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["seed", "check", "status"])
    ap.add_argument("--src", type=Path, default=book.SRC)
    ap.add_argument("--seed", type=Path, default=book.SEED)
    ap.add_argument("--reseed", action="store_true")
    ap.add_argument("-n", type=int, default=20)
    a = ap.parse_args(argv)
    if a.cmd != "status" and not a.src.exists():
        print(f"no workbook at {a.src}", file=sys.stderr)
        return 2
    box: dict = {}

    def work():
        if a.cmd == "seed":
            box["out"] = book.seed(a.src, a.seed)
        elif a.cmd == "status":
            live = book.load(a.seed)
            box["out"] = {"error": "no seed"} if live is None else {
                **live.meta, "writes_applied": live.applied, "through": live.through,
                "unknown_writes": len(live.unknown), "stale": book.stale(a.seed, a.src)}
        else:
            r = check(a.src, a.seed, a.n)
            if a.reseed and "error" not in r:
                # Clean or not: while Excel is still the workbook, what it
                # saved is the truth, and a difference is a hand edit to adopt
                # (it stays in the log line above the reseed).
                r["reseeded"] = book.seed(a.src, a.seed)["saved"]
            box["out"] = r

    threading.stack_size(512 * 1024 * 1024)      # date columns chain a row per day
    th = threading.Thread(target=work)
    th.start()
    th.join()
    out = box.get("out")
    if out is None:
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if a.cmd == "check" and "error" not in out:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a") as f:
                f.write(json.dumps({k: v for k, v in out.items() if out.get("clean") is False or not k.endswith("_sample")},
                                   ensure_ascii=False) + "\n")
        except OSError:
            pass
        return 0 if out["clean"] else 1
    return 2 if "error" in out else 0


if __name__ == "__main__":
    sys.exit(main())
