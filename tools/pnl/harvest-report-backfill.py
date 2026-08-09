#!/usr/bin/env python3
"""Harvest archived per-property P&L reports into a pre-cutover backfill file.

AppFolio's GL was restated around June 2026: income-statement queries for
months before 2026-06 now return ~$0 (those books lived in QBO, which is not
reachable). But the reports generated in April-June 2026 by pnl-fast.py
captured AppFolio's monthly numbers while they were still there, in exactly
the row labels the pipeline still uses. This script walks those archived
reports and extracts every pre-cutover month's label->value rows.

Output: tools/pnl/data/report-backfill.json
    {prop_code: {"YYYY-MM": {label: value, ...}, ...}, ...}

Dedup: files are processed oldest-generation first, so for any (prop, month)
the NEWEST archived report wins (later generations had later data).

Values are the rounded integers printed in the report tables — a few dollars
of rounding drift vs the original floats is accepted and noted in the
regenerated reports' provenance line.

Usage:
    python3 harvest-report-backfill.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

REPORTS_ROOT = os.path.expanduser("~/vault/m5x2/reports/2026")
# Period dirs that predate the cutover, oldest first (later files override).
PERIOD_DIRS = ["03-March", "04-April", "05-May"]
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "report-backfill.json")
CUTOVER = "2026-06"  # first month AppFolio's live GL is trustworthy

MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

# Row labels worth harvesting — the exact denominations pnl-fast.py emits.
# Bold total rows (Total Income/OpEx/NOI/Deductions/Cashflow) are recomputed
# downstream and deliberately skipped.
VALID_LABELS = {
    "Rent Income", "Utility Reimb", "Late Fees", "Laundry", "Pet Rent/Fee",
    "Parking", "Move-In/Out", "Concessions", "Other Income",
    "Prop Mgmt", "Pest Control", "Insurance", "Prop Taxes", "R&M Repairs",
    "R&M Turns", "R&M Grounds", "Electric/Gas", "Water", "Garbage", "Other OpEx",
    "Mortgage Interest", "Mortgage Principal", "Legal",
    "CapEx Turns", "CapEx Appliances", "CapEx Disc", "CapEx Non-disc",
}

FNAME_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})-([a-z0-9]+)-trailing-12m-pnl\.md$")
HEADING_RE = re.compile(r"^## Trailing 12-Month P&L \((\w{3}) (\d{4}) . (\w{3}) (\d{4})\)", re.M)


def parse_num(s: str) -> float | None:
    s = s.strip().replace("**", "").replace(",", "")
    if not s or s in ("—", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def month_seq(start_y: int, start_m: int, n: int = 12) -> list[str]:
    out = []
    y, m = start_y, start_m
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def harvest_file(path: str) -> tuple[str, dict] | None:
    """→ (prop_code, {month: {label: value}}) for pre-cutover months, or None."""
    fname = os.path.basename(path)
    fm = FNAME_RE.match(fname)
    if not fm:
        return None
    prop = fm.group(4)

    with open(path, encoding="utf-8") as f:
        text = f.read()

    hm = HEADING_RE.search(text)
    if not hm:
        return None
    months = month_seq(int(hm.group(2)), MONTHS[hm.group(1)])

    # The P&L table is the first table whose header row starts "| Account |"
    # and has 12 month columns + T12.
    result: dict[str, dict] = {}
    in_table = False
    for line in text.splitlines():
        if line.startswith("| Account"):
            # 14 data columns expected: Account + 12 months + T12 (header cell
            # padding varies between generations). The Comparisons table also
            # starts "| Account" but has 11 differently-named columns —
            # column count is the discriminator.
            if len([c for c in line.split("|") if c.strip()]) == 14:
                in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break  # table ended
            cells = line.split("|")[1:-1]
            if len(cells) != 14:
                continue
            label = cells[0].strip().replace("**", "")
            if label not in VALID_LABELS:
                continue
            for mk, cell in zip(months, cells[1:13]):
                if mk >= CUTOVER:
                    continue
                v = parse_num(cell)
                if v is None:
                    continue
                result.setdefault(mk, {})[label] = v
    return (prop, result) if result else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = []
    for period in PERIOD_DIRS:
        pdir = os.path.join(REPORTS_ROOT, period)
        if not os.path.isdir(pdir):
            continue
        for root, _dirs, fnames in os.walk(pdir):
            for fn in sorted(fnames):
                if FNAME_RE.match(fn):
                    files.append(os.path.join(root, fn))
    # Oldest generation first so newest wins on overlap. Generation date is
    # embedded in the filename; period dir order is already oldest-first,
    # but sort globally by (generation date, path) to be exact.
    files.sort(key=lambda p: os.path.basename(p))

    backfill: dict[str, dict] = {}
    for path in files:
        got = harvest_file(path)
        if not got:
            continue
        prop, months_data = got
        dest = backfill.setdefault(prop, {})
        for mk, labels in months_data.items():
            dest[mk] = labels  # newest file wins wholesale for that month

    props = sorted(backfill)
    all_months = sorted({mk for p in backfill.values() for mk in p})
    print(f"harvested {len(files)} report files -> {len(props)} properties, "
          f"months {all_months[0]}..{all_months[-1]}" if props else "nothing harvested")
    for p in props:
        mks = sorted(backfill[p])
        print(f"  {p}: {len(mks)} months ({mks[0]}..{mks[-1]})")

    if args.dry_run:
        return 0
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(backfill, f, separators=(",", ":"), sort_keys=True)
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
