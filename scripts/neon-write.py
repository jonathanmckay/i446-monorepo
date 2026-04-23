#!/usr/bin/env python3
"""
Write values to the live Neon spreadsheet via AppleScript on `ix`
(Excel must be open ON IX). All writes route through the shared
`_lib/ix-osa.py` helper — local osascript writes are forbidden
because they create OneDrive merge conflicts against the canonical
workbook on Ix.

Usage:
    python3 neon-write.py --sheet "0₦" --col D --date "3/27" --value 540
    python3 neon-write.py --sheet "0₦" --col D --value "+30"      # append to today
    python3 neon-write.py --sheet "0分" --col AA --value 120       # today, sheet 0分
"""

import argparse
import os
import sys
from datetime import datetime

# Date column per sheet
SHEET_DATE_COL = {
    "0₦": "C",
    "0n": "C",
    "0分": "B",
}

WORKBOOK = "Neon分v12.2.xlsx"

# Import the shared ix helper.
_LIB = os.path.expanduser("~/.claude/skills/_lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("ix_osa", os.path.join(_LIB, "ix-osa.py"))
    ix_osa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ix_osa)
except Exception as e:  # pragma: no cover
    print(f"Error: cannot load ix-osa helper from {_LIB}: {e}", file=sys.stderr)
    sys.exit(1)


def _today_md():
    """Return today as M/D (no leading zeros)."""
    now = datetime.now()
    return f"{now.month}/{now.day}"


def _run_applescript(script):
    res = ix_osa.run(script)
    if res.returncode != 0:
        # Surface the helper's error verbatim and abort.
        msg = (res.stderr or res.stdout or "ix-osa failed").strip()
        print(msg, file=sys.stderr)
        sys.exit(res.returncode or 1)
    return res.stdout.strip()


def write_value(sheet, col, date_str, value_str):
    date_col = SHEET_DATE_COL.get(sheet)
    if not date_col:
        print(f"Error: unknown sheet '{sheet}'. Known: {', '.join(SHEET_DATE_COL)}", file=sys.stderr)
        sys.exit(1)

    append_mode = value_str.startswith("+")

    if append_mode:
        append_val = value_str
        script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of workbook "{WORKBOOK}"
    set todayRow to 0
    repeat with i from 2 to 400
        if (string value of cell ("{date_col}" & i) of theSheet) = "{date_str}" then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow = 0 then
        return "ERROR:date_not_found"
    end if
    set theCell to cell ("{col}" & todayRow) of theSheet
    set oldFormula to formula of theCell
    if oldFormula = "" then
        set formula of theCell to "{append_val.lstrip('+')}"
    else
        set formula of theCell to oldFormula & "{append_val}"
    end if
    return "OK:" & todayRow & ":" & (formula of theCell)
end tell
'''
    else:
        script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of workbook "{WORKBOOK}"
    set todayRow to 0
    repeat with i from 2 to 400
        if (string value of cell ("{date_col}" & i) of theSheet) = "{date_str}" then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow = 0 then
        return "ERROR:date_not_found"
    end if
    set theCell to cell ("{col}" & todayRow) of theSheet
    set formula of theCell to "{value_str}"
    return "OK:" & todayRow & ":" & (formula of theCell)
end tell
'''

    result = _run_applescript(script)

    if result.startswith("ERROR:date_not_found"):
        print(f"Error: date '{date_str}' not found in {sheet} column {date_col}", file=sys.stderr)
        sys.exit(1)

    parts = result.split(":", 2)
    if len(parts) >= 3:
        row = parts[1]
        formula = parts[2]
        mode = "appended" if append_mode else "set"
        print(f"{mode} {col}{row} = {formula}")
    else:
        print(result)


def main():
    parser = argparse.ArgumentParser(description="Write to Neon spreadsheet via AppleScript on ix")
    parser.add_argument("--sheet", required=True, help='Sheet name (e.g. "0₦" or "0分")')
    parser.add_argument("--col", required=True, help="Target column letter (e.g. D, AA)")
    parser.add_argument("--date", help="Date as M/D (e.g. 3/27). Default: today")
    parser.add_argument("--value", required=True, help='Value to write. Prefix with + to append (e.g. "+30")')
    args = parser.parse_args()

    date_str = args.date or _today_md()
    write_value(args.sheet, args.col, date_str, args.value)


if __name__ == "__main__":
    main()

