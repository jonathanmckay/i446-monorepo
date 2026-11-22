#!/usr/bin/env python3
"""calc_check: does lib/neon/calc.py compute the Neon workbook the way Excel does?

Loads a COPY of the saved workbook, evaluates every formula from the cells
alone, and compares each result with the value Excel cached in the file.
Read-only: nothing is written to the workbook, the daemon or the ledger.

  calc_check.py                     summary per sheet
  calc_check.py --sheet 0分 -n 40   mismatches on one sheet
  calc_check.py --json out.json     machine-readable result

Exit 0 when every formula matches, 1 otherwise.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from neon import calc  # noqa: E402

SRC = Path.home() / "Library" / "CloudStorage" / "OneDrive-Personal" / "vault-excel" / "Neon分v12.2.xlsx"
TOL = 1e-6


def same(got, want) -> bool:
    if isinstance(got, calc.Arr):
        got = got.rows[0][0] if got.rows and got.rows[0] else None
    if isinstance(want, calc.Err) or isinstance(got, calc.Err):
        return isinstance(want, calc.Err) and isinstance(got, calc.Err) and want.code == got.code
    if isinstance(want, bool) or isinstance(got, bool):
        return isinstance(want, bool) and isinstance(got, bool) and want == got
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(got - want) <= TOL * max(1.0, abs(want))
    if want in (None, "") and got in (None, ""):
        return True
    if want is None and got == 0:
        return True                    # Excel stored no value for a formula that is 0/blank
    return got == want


def run(src: Path, today: dt.date | None) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="calc-check-"))
    try:
        copy = tmp / "neon.xlsx"
        shutil.copy2(src, copy)
        saved = dt.datetime.fromtimestamp(os.path.getmtime(src))
        t0 = time.time()
        book, cached = calc.load_xlsx(copy, today=today or saved.date())
        t_load = time.time() - t0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    per = collections.defaultdict(lambda: {"formulas": 0, "ok": 0, "mismatch": 0, "error": 0})
    bad, reasons = [], collections.Counter()
    t0 = time.time()
    for key in sorted(cached):
        sheet, col, row = key
        s = per[sheet]
        s["formulas"] += 1
        try:
            got = book.value(sheet, col, row)
        except calc.CalcError as e:
            s["error"] += 1
            reasons[str(e)[:60]] += 1
            bad.append({"cell": f"{sheet}!{calc.col_letter(col)}{row}", "formula": str(book.content(sheet, col, row))[:160],
                        "error": str(e)[:120], "excel": repr(cached[key])})
            continue
        except RecursionError:
            s["error"] += 1
            reasons["recursion"] += 1
            continue
        if same(got, cached[key]):
            s["ok"] += 1
        else:
            s["mismatch"] += 1
            bad.append({"cell": f"{sheet}!{calc.col_letter(col)}{row}", "formula": str(book.content(sheet, col, row))[:160],
                        "got": repr(got)[:60], "excel": repr(cached[key])[:60]})
    return {"src": str(src), "saved": saved.isoformat(timespec="seconds"), "load_s": round(t_load, 2),
            "eval_s": round(time.time() - t0, 2), "sheets": dict(per), "bad": bad,
            "reasons": reasons.most_common(20)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--sheet")
    ap.add_argument("-n", type=int, default=15, help="mismatches to print")
    ap.add_argument("--today", help="YYYY-MM-DD for TODAY() (default: the file's save date)")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    today = dt.date.fromisoformat(a.today) if a.today else None
    box = {}
    threading.stack_size(512 * 1024 * 1024)        # running totals recurse a row per day
    th = threading.Thread(target=lambda: box.update(run(a.src, today)))
    th.start()
    th.join()
    if not box:
        print("calc_check: the run died (see the traceback above)", file=sys.stderr)
        return 2
    tot = collections.Counter()
    print(f"{box['src']}  saved {box['saved']}  load {box['load_s']}s  eval {box['eval_s']}s")
    for sheet, s in box["sheets"].items():
        tot.update(s)
        print(f"  {sheet:10s} {s['formulas']:6d} formulas  {s['ok']:6d} ok  {s['mismatch']:5d} differ  {s['error']:5d} unsupported")
    pct = 100.0 * tot["ok"] / max(1, tot["formulas"])
    print(f"  {'total':10s} {tot['formulas']:6d} formulas  {tot['ok']:6d} ok  {tot['mismatch']:5d} differ  "
          f"{tot['error']:5d} unsupported  ({pct:.2f}%)")
    for reason, n in box["reasons"]:
        print(f"  unsupported x{n}: {reason}")
    shown = [b for b in box["bad"] if not a.sheet or b["cell"].startswith(a.sheet + "!")][:a.n]
    for b in shown:
        print("  " + json.dumps(b, ensure_ascii=False))
    if a.json:
        a.json.write_text(json.dumps(box, ensure_ascii=False, indent=1))
    return 0 if tot["ok"] == tot["formulas"] else 1


if __name__ == "__main__":
    sys.exit(main())
