#!/usr/bin/env python3
"""
Write values to the live Neon spreadsheet via AppleScript (Excel must be open).

Usage:
    python3 neon-write.py --sheet "0₦" --col D --date "3/27" --value 540
    python3 neon-write.py --sheet "0₦" --col D --value "+30"      # append to today
    python3 neon-write.py --sheet "0分" --col AA --value 120       # today, sheet 0分
"""

import argparse
import subprocess
import sys
from datetime import datetime

# Date column per sheet
SHEET_DATE_COL = {
    "0₦": "C",
    "0分": "B",
}

NEON_SYMLINK = "~/OneDrive/vault-excel/Neon-current.xlsx"
NEON_HARDCODED = "~/OneDrive/vault-excel/Neon分v12.2.xlsx"


def _today_md():
    """Return today as M/D (no leading zeros)."""
    now = datetime.now()
    return f"{now.month}/{now.day}"


def _run_applescript(script):
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        print(f"AppleScript error: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def write_value(sheet, col, date_str, value_str):
    date_col = SHEET_DATE_COL.get(sheet)
    if not date_col:
        print(f"Error: unknown sheet '{sheet}'. Known: {', '.join(SHEET_DATE_COL)}", file=sys.stderr)
        sys.exit(1)

    append_mode = value_str.startswith("+")

    if append_mode:
        # Read existing formula, then append
        append_val = value_str  # keep the "+30" as-is for formula concat
        script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of active workbook
    set todayRow to 0
    repeat with i from 2 to 400
        if (string value of cell ("{date_col}" & i) of theSheet) = "{date_str}" then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow = 0 then
        return "ERR:date_not_found"
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
        # Direct write
        script = f'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of active workbook
    set todayRow to 0
    repeat with i from 2 to 400
        if (string value of cell ("{date_col}" & i) of theSheet) = "{date_str}" then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow = 0 then
        return "ERR:date_not_found"
    end if
    set theCell to cell ("{col}" & todayRow) of theSheet
    set formula of theCell to "{value_str}"
    return "OK:" & todayRow & ":" & (formula of theCell)
end tell
'''

    result = _run_applescript(script)

    if result.startswith("ERR:date_not_found"):
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
    parser = argparse.ArgumentParser(description="Write to Neon spreadsheet via AppleScript")
    parser.add_argument("--sheet", required=True, help='Sheet name (e.g. "0₦" or "0分")')
    parser.add_argument("--col", required=True, help="Target column letter (e.g. D, AA)")
    parser.add_argument("--date", help="Date as M/D (e.g. 3/27). Default: today")
    parser.add_argument("--value", required=True, help='Value to write. Prefix with + to append (e.g. "+30")')
    args = parser.parse_args()

    date_str = args.date or _today_md()
    write_value(args.sheet, args.col, date_str, args.value)


if __name__ == "__main__":
    main()
