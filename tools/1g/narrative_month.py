#!/usr/bin/env python3
"""narrative_month.py: create the monthly 2g+1g narrative doc ahead of time.

    narrative_month.py              ensure this week's and next week's docs exist
    narrative_month.py 2026-11      ensure that month's doc exists
    narrative_month.py --dry-run    print what would be created, write nothing

A week belongs to the month its Sunday falls in (same rule as 1g_review.py's
doc_path), so "Sep 27 – Oct 3" lives in the September doc. Run daily from Ix
cron: next month's doc appears the Sunday before its first week starts, with
every week heading pre-filled, and gets linked from 5e2.md. Idempotent: an
existing doc is never touched.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_S = importlib.util.spec_from_file_location("r1g", Path(__file__).parent / "1g_review.py")
r1g = importlib.util.module_from_spec(_S)
_S.loader.exec_module(r1g)

INDEX = r1g.NARRATIVE_DIR / "5e2.md"


def month_sundays(year: int, month: int) -> list[date]:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)  # first Sunday
    out = []
    while d.month == month:
        out.append(d)
        d += timedelta(days=7)
    return out


def render(year: int, month: int, today: date) -> str:
    sundays = month_sundays(year, month)
    prev = r1g.doc_path(sundays[0] - timedelta(days=7))
    prev_link = f" Previous month: [[{prev.stem}]]." if prev.exists() else ""
    weeks = "\n".join(
        f"- **{s:%b} {s.day} – {(s + timedelta(days=6)):%b} {(s + timedelta(days=6)).day}** —"
        for s in sundays)
    return f"""---
title: "2g+1g Narrative"
date: {today}
type: doc
tags: [g245, 2g, 1g, goals]
source: tools/1g/narrative_month.py
status: active
updated: {today}
---

Monthly (2g) goals as narrative, with weekly (1g) detail: each month gets a section with its weekly arc and themes.{prev_link}

## {year}-{month:02d} ({date(year, month, 1):%B})

### Weekly arc

{weeks}

### Themes

-
"""


def link_in_index(path: Path, text: str, today: date) -> str:
    """Put `- [[stem|YYYY.MM]]` at the top of that year's list in 5e2.md,
    creating the year's heading above the previous year's if needed."""
    stem = path.stem
    if f"[[{stem}" in text:
        return text
    year = stem[:4]
    line = f"- [[{stem}|{stem.split(' ')[0]}]]"
    heading = f"## {year} monthly narratives (2g + weekly 1g)"
    if heading in text:
        text = text.replace(heading + "\n", f"{heading}\n{line}\n", 1)
    else:
        m = re.search(r"^## \d{4} monthly narratives", text, re.M)
        block = f"{heading}\n{line}\n\n"
        text = text[:m.start()] + block + text[m.start():] if m else text.rstrip("\n") + "\n\n" + block
    return re.sub(r"^updated: .*$", f"updated: {today}", text, count=1, flags=re.M)


def ensure(year: int, month: int, today: date, dry_run: bool = False) -> str | None:
    path = r1g.doc_path(month_sundays(year, month)[0])
    if path.exists():
        return None
    if not dry_run:
        path.write_text(render(year, month, today))
        if INDEX.exists():
            INDEX.write_text(link_in_index(path, INDEX.read_text(), today))
    return str(path)


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    today = datetime.now(r1g.TZ).date()
    if args:
        y, m = (int(x) for x in args[0].split("-"))
        targets = [(y, m)]
    else:
        this_sun = today - timedelta(days=(today.weekday() + 1) % 7)
        targets = sorted({(s.year, s.month) for s in (this_sun, this_sun + timedelta(days=7))})
    for y, m in targets:
        made = ensure(y, m, today, dry)
        if made:
            print(("would create " if dry else "created ") + made)
    return 0


if __name__ == "__main__":
    sys.exit(main())
