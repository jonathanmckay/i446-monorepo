#!/usr/bin/env python3
"""Adopt hand-typed Excel edits (hcbi by default) into the Neon ledger.

Runs at the end of scripts/neon-reconcile.py (Ix cron 23:55 and 03:50), right
after the reconcile has journaled Excel's edits and before the 04:05 strict
diff. Never writes Excel. See lib/neon/adopt.py.

Usage:
  neon-adopt-hand.py                    # yesterday + today, hcbi
  neon-adopt-hand.py 2026-11-02 [...]   # explicit days
  neon-adopt-hand.py --sheet hcbi --sheet 0n --dry-run
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from neon import adopt  # noqa: E402


def default_dates(today=None) -> list:
    today = today or _dt.date.today()
    return [(today - _dt.timedelta(days=1)).isoformat(), today.isoformat()]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="neon-adopt-hand")
    p.add_argument("dates", nargs="*", help="YYYY-MM-DD (default: yesterday, today)")
    p.add_argument("--sheet", action="append", dest="sheets")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    dates = a.dates or default_dates()
    out = adopt.run(dates, sheets=a.sheets or adopt.DEFAULT_SHEETS, dry_run=a.dry_run)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    verb = "would adopt" if a.dry_run else "adopted"
    print(f"{stamp} hand-adopt {','.join(dates)} {verb}={len(out)}", flush=True)
    for e in out:
        print(f"  {e['sheet']}!{e['col']}{e['row']} {e['date']}: "
              f"ledger {e['ledger_before']!r} -> excel {e['after']!r}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
