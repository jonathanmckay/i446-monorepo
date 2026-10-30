#!/usr/bin/env python3
"""Journaled cell writes to one row of a Neon review sheet, for skills.

The weekly review skills (/1i9, /1m5x2, /1s897) used to pipe hand-filled
AppleScript through ix-osa.sh, which bypasses the neon ledger. This CLI is
their write step: it resolves and verifies the row, then sends every cell in
one excel-http /batch_write (Neon-off-Excel step 2c deferred items,
2026-10-30). Numbers stay numbers; prose goes as mode "text" (never a
formula, newlines kept).

    neon-review-write.py --sheet i9+m5x2 --row 44 --label 10.3 --src 1i9 \
        B=MA D=@/tmp/1i9-done.txt E=@/tmp/1i9-exceed.txt F=2134
    neon-review-write.py --sheet 0s897 --date 2026-10-21 --merge-col Z \
        --src 1s897 Z=340 AA=@/tmp/1s897-aa.txt

Cell specs are COL=value: `@path` reads prose from a file (the safe way to
pass multi-line text), a bare number is written as a number, anything else is
text. --label checks the row's week label (col A by default) before writing;
--merge-col checks that the row is the top of that column's merged range
(read-only probe over ix-osa). Exit 1 on any mismatch, nothing written.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
from neon import excel  # noqa: E402

IX_OSA = Path.home() / ".claude/skills/_lib/ix-osa.sh"
WORKBOOK = "Neon分v12.2.xlsx"
COL_RE = re.compile(r"^[A-Z]{1,3}$")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
# The daemon runs each /batch_write as one osascript call under a 15 s cap;
# 0s.py found four prose cells per call to be the safe size (2026-10-26).
CHUNK = 4


def parse_cell(spec: str) -> dict:
    col, sep, raw = spec.partition("=")
    col = col.strip().upper()
    if not sep or not COL_RE.match(col):
        raise ValueError(f"bad cell spec {spec!r} (want COL=value)")
    if raw.startswith("@"):
        text = Path(raw[1:]).expanduser().read_text(encoding="utf-8").strip("\n")
        return {"col": col, "value": text, "mode": "text"}
    if NUM_RE.match(raw.strip()):
        n = float(raw)
        return {"col": col, "value": int(n) if n.is_integer() else n}
    return {"col": col, "value": raw, "mode": "text"}


def same_label(got: str, want: str) -> bool:
    """Week labels are formulas like =A43+0.1, so compare as numbers."""
    try:
        return round(float(got), 2) == round(float(want), 2)
    except (TypeError, ValueError):
        return str(got).strip() == str(want).strip()


def mdy(d: dt.date) -> str:
    """M/D/YY: year-aware, so 0s897!B's two Januaries cannot collide."""
    return f"{d.month}/{d.day}/{d:%y}"


def merge_top(sheet: str, col: str, row: int) -> int:
    """First row of the merged range holding col<row> (read-only)."""
    script = (
        'tell application "Microsoft Excel"\n'
        f'  set ws to worksheet "{sheet}" of workbook "{WORKBOOK}"\n'
        f'  return (first row index of (merge area of range "{col}{row}" of ws)) as text\n'
        'end tell')
    p = subprocess.run([str(IX_OSA)], input=script, capture_output=True, text=True, timeout=60)
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out.isdigit():
        raise RuntimeError(f"merge probe failed: {out or p.stderr.strip()}")
    return int(out)


def write(sheet: str, row: int, cells: list[dict], *, src: str, key: str | None) -> dict:
    """batch_write in chunks. The client's daemon-down fallback is a per-cell
    ssh write that turns a leading "=" into a formula, so prose starting with
    "=" refuses the fallback instead."""
    risky = any(c.get("mode") == "text" and str(c["value"]).startswith("=") for c in cells)
    results = []
    for i in range(0, len(cells), CHUNK):
        r = excel.batch_write(sheet, cells[i:i + CHUNK], row=row, date=key, src=src,
                              fallback=not risky)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or r, "written": results}
        results.extend(r.get("results") or [])
    return {"ok": True, "row": row, "results": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="journaled writes to one Neon review-sheet row")
    ap.add_argument("--sheet", required=True)
    tgt = ap.add_mutually_exclusive_group(required=True)
    tgt.add_argument("--row", type=int)
    tgt.add_argument("--date", help="YYYY-MM-DD (row found by the sheet's date column)")
    ap.add_argument("--label", help="expected week label in --label-col; abort if different")
    ap.add_argument("--label-col", default="A")
    ap.add_argument("--merge-col", help="abort unless the row is the top of this column's merge")
    ap.add_argument("--src", required=True, help="ledger label (who wrote it)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("cells", nargs="+", help="COL=value | COL=@file | COL=<number>")
    a = ap.parse_args(argv)

    try:
        cells = [parse_cell(s) for s in a.cells]
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    key = None
    row = a.row
    if a.date:
        key = mdy(dt.date.fromisoformat(a.date))
        row = excel.lookup_row(a.sheet, key)
        if not row:
            print(f"ERROR: {key} not found in {a.sheet}", file=sys.stderr)
            return 1
    if a.label:
        got = excel.read(a.sheet, a.label_col, row=row)
        if not got.get("ok"):
            print(f"ERROR: could not read {a.sheet}!{a.label_col}{row}: {got.get('error') or got}",
                  file=sys.stderr)
            return 1
        if not same_label(got.get("value"), a.label):
            print(f"ERROR: {a.sheet}!{a.label_col}{row} is {got.get('value')!r}, expected "
                  f"{a.label!r}. Nothing written.", file=sys.stderr)
            return 1
        key = a.label  # journaled as `week` on label sheets
    if a.merge_col:
        try:
            top = merge_top(a.sheet, a.merge_col.upper(), row)
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        if top != row:
            print(f"ERROR: {a.sheet}!{a.merge_col}{row} is not the top of its merged range "
                  f"(top row {top}). Nothing written.", file=sys.stderr)
            return 1

    if a.dry_run:
        print(json.dumps({"sheet": a.sheet, "row": row, "key": key, "cells": cells},
                         ensure_ascii=False, indent=1))
        return 0
    r = write(a.sheet, row, cells, src=a.src, key=key)
    if not r["ok"]:
        done = ",".join(x.get("col", "?") for x in r["written"]) or "none"
        print(f"ERROR: {a.sheet} row {row} write failed ({r['error']}); cells already "
              f"written: {done}", file=sys.stderr)
        return 1
    broken = [x.get("col") for x in r["results"] if x.get("chain") == "broken"]
    print(f"OK: {a.sheet} row {row} ← {','.join(c['col'] for c in cells)}"
          + (f" (chain broken: {','.join(broken)}; cell was edited outside the daemon)"
             if broken else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
