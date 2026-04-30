#!/usr/bin/env python3
"""ix-chart-insert.py — insert a PNG chart into Neon分v12.2.xlsx on ix.

Runs LOCALLY: scp's the PNG to ix:~/tmp/charts/ and then invokes a small
xlwings (or AppleScript fallback) snippet on ix via ssh. Never opens
the local Excel copy. Hard-fails if ix is unreachable.

Usage:
    ix-chart-insert.py --png /path/to/chart.png \
                       --workbook 'Neon分v12.2.xlsx' \
                       --sheet '0分' \
                       --cell A1 \
                       [--width 432] [--height 432]

Exit codes: 0 ok; 3 ix unreachable; 5 scp/insert failed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

IX_HOST = os.environ.get("IX_HOST", "ix")
REMOTE_DIR = "~/tmp/charts"
UNREACHABLE_MSG = (
    "ERROR: ix unreachable — chart insertion aborted to prevent "
    "OneDrive merge conflict. Restore SSH to ix and retry."
)


def _ssh(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", IX_HOST, *args],
        text=True, capture_output=True, **kw,
    )


def _scp(local: Path, remote: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["scp", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes",
         str(local), f"{IX_HOST}:{remote}"],
        text=True, capture_output=True,
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--png", required=True, type=Path)
    p.add_argument("--workbook", default="Neon分v12.2.xlsx")
    p.add_argument("--sheet", required=True)
    p.add_argument("--cell", required=True)
    p.add_argument("--width", type=float, default=45.0)
    p.add_argument("--height", type=float, default=45.0)
    args = p.parse_args()

    if not args.png.exists():
        print(f"ERROR: PNG not found: {args.png}", file=sys.stderr)
        return 5

    # Ensure remote dir exists and copy the PNG.
    mk = _ssh("mkdir", "-p", REMOTE_DIR)
    if mk.returncode == 255:
        print(UNREACHABLE_MSG, file=sys.stderr)
        return 3
    if mk.returncode != 0:
        print(f"ERROR: remote mkdir failed: {mk.stderr.strip()}", file=sys.stderr)
        return 5

    remote_png = f"{REMOTE_DIR}/{args.png.name}"
    cp = _scp(args.png, remote_png)
    if cp.returncode == 255:
        print(UNREACHABLE_MSG, file=sys.stderr)
        return 3
    if cp.returncode != 0:
        print(f"ERROR: scp failed: {cp.stderr.strip()}", file=sys.stderr)
        return 5

    # Try xlwings on ix first; fall back to AppleScript `add picture`.
    py_snippet = f"""
import os, sys
png = os.path.expanduser({remote_png!r})
try:
    import xlwings as xw
except Exception:
    print("NO_XLWINGS"); sys.exit(0)
try:
    wb = xw.Book({args.workbook!r})
except Exception:
    for b in xw.books:
        if b.name == {args.workbook!r}:
            wb = b; break
    else:
        print("ERROR: workbook not open on ix"); sys.exit(2)
sht = wb.sheets[{args.sheet!r}]
cell = sht.range({args.cell!r})
sht.pictures.add(png, left=cell.left, top=cell.top,
                 width={args.width}, height={args.height})
wb.save()
print("OK: inserted via xlwings")
"""
    r = _ssh("python3", "-", input=py_snippet)
    if r.returncode == 255:
        print(UNREACHABLE_MSG, file=sys.stderr)
        return 3

    if "NO_XLWINGS" in r.stdout:
        # AppleScript fallback (still on ix).
        applescript = f'''
tell application "Microsoft Excel"
    set wb to workbook "{args.workbook}"
    set sht to sheet "{args.sheet}" of wb
    set tgt to range "{args.cell}" of sht
    set pic to make new picture at sht with properties ¬
        {{file name:(POSIX file (do shell script "echo " & quoted form of "{remote_png}" & " | sed 's:^~:" & POSIX path of (path to home folder) & ":'") as alias)}}
    set left position of pic to (left position of tgt)
    set top position of pic to (top position of tgt)
    set width of pic to {args.width}
    set height of pic to {args.height}
    save wb
    return "OK: inserted via AppleScript"
end tell
'''
        r = _ssh("osascript", "-", input=applescript)
        if r.returncode == 255:
            print(UNREACHABLE_MSG, file=sys.stderr)
            return 3
        if r.returncode != 0 or not r.stdout.strip().startswith("OK"):
            print(f"ERROR: AppleScript insert failed: {r.stdout.strip()} {r.stderr.strip()}", file=sys.stderr)
            return 5
        print(r.stdout.strip())
        return 0

    if r.returncode != 0 or not r.stdout.strip().startswith("OK"):
        print(f"ERROR: xlwings insert failed: {r.stdout.strip()} {r.stderr.strip()}", file=sys.stderr)
        return 5
    print(r.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
