#!/usr/bin/env python3
"""
Write values to the live Neon spreadsheet through the excel-http daemon
client (`lib/neon/excel.py`). The client journals every write in the neon
audit ledger and falls back to one-shot ssh+osascript on ix when the
daemon is down, so this CLI works either way. Local osascript writes are
forbidden because they create OneDrive merge conflicts against the
canonical workbook on Ix.

Usage:
    python3 neon-write.py --sheet "0₦" --col D --date "3/27" --value 540
    python3 neon-write.py --sheet "0₦" --col D --value "+30"      # append to today
    python3 neon-write.py --sheet "0分" --col AA --value 120       # today, sheet 0分
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
from neon import excel

# Known sheets (date-row lookup handled by the daemon / client fallback)
KNOWN_SHEETS = ("0₦", "0n", "0分")


def _today_md():
    """Return today as M/D (no leading zeros)."""
    now = datetime.now()
    return f"{now.month}/{now.day}"


def write_value(sheet, col, date_str, value_str):
    if sheet not in KNOWN_SHEETS:
        print(f"Error: unknown sheet '{sheet}'. Known: {', '.join(KNOWN_SHEETS)}", file=sys.stderr)
        sys.exit(1)

    append_mode = value_str.startswith("+")

    if append_mode:
        res = excel.append(sheet, col, date=date_str, value=value_str,
                           src="neon-write-cli")
    else:
        res = excel.write(sheet, col, date=date_str, value=value_str,
                          src="neon-write-cli")

    if not res.get("ok"):
        err = str(res.get("error") or "write failed")
        if "date_not_found" in err:
            print(f"Error: date '{date_str}' not found in {sheet}", file=sys.stderr)
        else:
            print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

    mode = "appended" if append_mode else "set"
    print(f"{mode} {col}{res.get('row')} = {res.get('formula')}")


def main():
    parser = argparse.ArgumentParser(description="Write to Neon spreadsheet via the excel-http daemon on ix")
    parser.add_argument("--sheet", required=True, help='Sheet name (e.g. "0₦" or "0分")')
    parser.add_argument("--col", required=True, help="Target column letter (e.g. D, AA)")
    parser.add_argument("--date", help="Date as M/D (e.g. 3/27). Default: today")
    parser.add_argument("--value", required=True, help='Value to write. Prefix with + to append (e.g. "+30")')
    args = parser.parse_args()

    date_str = args.date or _today_md()
    write_value(args.sheet, args.col, date_str, args.value)


if __name__ == "__main__":
    main()
