#!/usr/bin/env python3
"""Add points to today's row in Neon分v12.2.xlsx via ix.

All writes route through ~/.claude/skills/_lib/ix-osa.py — local
xlwings/osascript writes against the OneDrive copy are forbidden
because they create merge conflicts against the canonical workbook
on Ix.
"""

import os
import sys
from datetime import datetime

WORKBOOK = "Neon分v12.2.xlsx"
SHEET = "0₦"
DATE_COL = "C"
COLUMN_MAP = {
    "代": "X",
    "代码": "X",
    "m5x2": "BE",
}

_LIB = os.path.expanduser("~/.claude/skills/_lib")
sys.path.insert(0, _LIB)
import importlib.util
_spec = importlib.util.spec_from_file_location("ix_osa", os.path.join(_LIB, "ix-osa.py"))
ix_osa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ix_osa)


def add_points(column_name: str, points: int) -> int:
    col = COLUMN_MAP.get(column_name, column_name)
    today = datetime.now()
    md = f"{today.month}/{today.day}"

    script = f'''
tell application "Microsoft Excel"
    set ws to sheet "{SHEET}" of workbook "{WORKBOOK}"
    set todayRow to 0
    repeat with r from 3 to 500
        set cellDate to value of cell 3 of row r of ws
        if cellDate is not missing value then
            try
                set m to (month of (cellDate as date)) as integer
                set d to day of (cellDate as date)
                if m = {today.month} and d = {today.day} then
                    set todayRow to r
                    exit repeat
                end if
            end try
        end if
    end repeat
    if todayRow = 0 then return "ERROR: date {md} not found"
    set theCell to range ("{col}" & todayRow) of ws
    set cur to value of theCell
    if cur is missing value then set cur to 0
    set newVal to (cur as number) + {points}
    set value of theCell to newVal
    return "OK: {col}" & todayRow & " " & cur & " -> " & newVal
end tell
'''
    res = ix_osa.run(script)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout or "ix-osa failed").strip()
        print(msg, file=sys.stderr)
        return res.returncode or 1
    print(res.stdout.strip())
    return 0


if __name__ == "__main__":
    col = sys.argv[1] if len(sys.argv) > 1 else "代"
    pts = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    sys.exit(add_points(col, pts))

