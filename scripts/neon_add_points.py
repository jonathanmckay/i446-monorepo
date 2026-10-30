#!/usr/bin/env python3
"""Add points to a column of today's 0n row in Neon分v12.2.xlsx.

Through the excel-http daemon (`lib/neon/excel.py`, mode "add": numeric
read-modify-write, journaled in the neon ledger) since 2026-10-30; the old
raw ix-osa AppleScript also targeted a sheet named "0₦" that no longer
exists. `add` needs the daemon: with the daemon down this fails rather than
guessing.
"""

import os
import sys
from datetime import datetime

COLUMN_MAP = {
    "代": "X",
    "代码": "X",
    "m5x2": "BE",
}

sys.path.insert(0, os.path.expanduser("~/i446-monorepo/lib"))
from neon import excel  # noqa: E402


def add_points(column_name: str, points: int) -> int:
    col = COLUMN_MAP.get(column_name, column_name)
    today = datetime.now()
    md = f"{today.month}/{today.day}"
    r = excel.batch_write("0n", [{"col": col, "value": points, "mode": "add"}],
                          date=md, src=f"neon_add_points {column_name}")
    res = (r.get("results") or [{}])[0]
    if not r.get("ok"):
        print(f"ERROR: {res.get('error') or r.get('error') or r}", file=sys.stderr)
        return 1
    print(f"OK: {col}{r.get('row')} {res.get('before_value')} -> {res.get('value')}")
    return 0


if __name__ == "__main__":
    col = sys.argv[1] if len(sys.argv) > 1 else "代"
    pts = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    sys.exit(add_points(col, pts))
