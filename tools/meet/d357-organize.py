#!/usr/bin/env python3
"""d357-organize.py — move loose d357 notes into week folders.

Filename convention: YYYY.MM.DD-<slug>.md at the root of ~/vault/d357/.
Target: ~/vault/d357/<M.W>/<same-name>.md

Week math matches 1n+ (Sunday-anchored):
    sunday = date - timedelta(days=(date.weekday()+1) % 7)
    M = sunday.month, W = (sunday.day - 1) // 7 + 1

This script is idempotent and side-effect-light: it only moves files; it
never edits content. Safe to run on a cron.

Usage:
    python3 d357-organize.py            # move loose files
    python3 d357-organize.py --dry-run  # preview
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path.home() / "vault" / "d357"
DATE_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})-")


def week_folder(d: date) -> str:
    sunday = d - timedelta(days=(d.weekday() + 1) % 7)
    return f"{sunday.month}.{(sunday.day - 1) // 7 + 1}"


def parse_date(name: str) -> date | None:
    m = DATE_RE.match(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not ROOT.is_dir():
        print(f"no d357 root at {ROOT}", file=sys.stderr)
        return 0

    moved = skipped = 0
    for f in sorted(ROOT.glob("*.md")):
        d = parse_date(f.name)
        if d is None:
            skipped += 1
            continue
        target_dir = ROOT / week_folder(d)
        target = target_dir / f.name
        if target.exists():
            print(f"SKIP exists: {target}", file=sys.stderr)
            skipped += 1
            continue
        print(f"{'[dry] ' if args.dry_run else ''}{f.name} → {target_dir.name}/")
        if not args.dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(target))
        moved += 1

    print(f"done: moved={moved} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
