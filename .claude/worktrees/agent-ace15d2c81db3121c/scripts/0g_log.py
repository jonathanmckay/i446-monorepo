#!/usr/bin/env python3
"""0g_log.py — durably record 0₲ goals to vault/g245/0g-log.md at SET time.

The daily reset's archiver (-1g-cron.py `_archive_0g_goals`) only captures goals
still sitting in the live `## 0₲` section when it runs at ~04:00. Goals routinely
leave that section first — moved to `### 以后的目标`, wiped by a reset, or lost to
a multi-machine vault-sync conflict — so they never reach the durable log
(regression 2026-06-26: a full day of 0₲ goals never logged). The robust fix is
to log goals the moment /0g sets them, independent of any later mutation.

This module is the single, idempotent entry point. Calling it repeatedly in a
day (the user runs /0g several times) MERGES new goals into today's section
without duplicating; goals already present are left untouched.

Usage:
    0g_log.py "tasks to 38 {40}" "do the test for both kids {60}"
    0g_log.py --day 2026-06-26 "stay cheerful {60}"   # backfill a past day
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

LOG_PATH = Path.home() / "vault" / "g245" / "0g-log.md"

_HEADER = (
    "---\n"
    'title: "0₲ Goals Log"\n'
    "date: {date}\n"
    "type: log\n"
    "tags: [g245, 0g]\n"
    "source: 0g\n"
    "---\n\n"
    "# 0₲ Daily Goals Log\n\n"
    "Each day's 0₲ goals, recorded when /0g sets them so they survive the daily "
    "reset, the 0₲→以后的目标 move, and vault-sync conflicts. Newest first.\n\n"
)

# A checkbox goal line: optional indent, "- [ ]"/"- [x]", then the goal text.
_CHECKBOX = re.compile(r"^\s*- \[[ xX]\]\s*(.*\S)\s*$")


def _goal_text(line: str) -> str:
    """The comparable body of a goal line: checkbox stripped, lowercased. Keeps
    (N)/[N]/{N} annotations so two genuinely different goals never collide, but
    two spellings of the same goal (checked vs unchecked) dedupe."""
    m = _CHECKBOX.match(line)
    body = m.group(1) if m else line.strip().lstrip("-").strip()
    return re.sub(r"\s+", " ", body).strip().lower()


def _normalize_goal(raw: str) -> str | None:
    """Turn a raw goal string (with or without a leading checkbox) into a single
    `- [ ] <text>` line. Returns None for blank/placeholder input."""
    s = raw.strip()
    if not s:
        return None
    # Strip a leading checkbox marker if present; a bare "- [ ]" placeholder
    # leaves no text and must never be logged.
    cb = re.match(r"^- \[[ xX]\]\s*(.*)$", s)
    text = (cb.group(1) if cb else s.lstrip("-").strip()).strip()
    if not text:
        return None  # empty placeholder, never log
    return f"- [ ] {text}"


def log_goals(goals: list[str], *, day: str | None = None,
              path: Path | None = None) -> dict:
    """Merge `goals` into `## {day}` of 0g-log.md (today by default).

    Idempotent: goals already recorded for the day (by normalized text) are
    skipped. Creates the day section newest-first if absent, and the whole file
    (with frontmatter) if missing. Returns {day, added, skipped}.
    """
    day = day or date.today().strftime("%Y.%m.%d")
    path = path or LOG_PATH

    new_lines = [g for g in (_normalize_goal(x) for x in goals) if g]
    if not new_lines:
        return {"day": day, "added": 0, "skipped": 0}

    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = _HEADER.format(date=day.replace(".", "-"))

    lines = text.split("\n")
    day_heading = f"## {day}"

    # Locate the day's section [start, end). end is the next "## " heading or EOF.
    start = next((i for i, l in enumerate(lines) if l.strip() == day_heading), None)
    added, skipped = [], 0
    if start is not None:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith("## ")), len(lines))
        existing = {_goal_text(l) for l in lines[start + 1:end] if _CHECKBOX.match(l)}
        for gl in new_lines:
            if _goal_text(gl) in existing:
                skipped += 1
                continue
            existing.add(_goal_text(gl))
            added.append(gl)
        if added:
            # Append after the last goal line in the section (before trailing blanks).
            insert = end
            while insert - 1 > start and not lines[insert - 1].strip():
                insert -= 1
            lines[insert:insert] = added
    else:
        # New day → insert a fresh section above the first existing date heading
        # (newest first), or at end of file if none exist yet.
        added = new_lines
        section = [day_heading, ""] + added + [""]
        insert_at = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
        lines[insert_at:insert_at] = section

    path.write_text("\n".join(lines), encoding="utf-8")
    return {"day": day, "added": len(added), "skipped": skipped}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Log 0₲ goals to 0g-log.md at set time.")
    ap.add_argument("goals", nargs="*", help="goal strings (with/without leading checkbox)")
    ap.add_argument("--day", help="YYYY-MM-DD or YYYY.MM.DD (default: today)")
    args = ap.parse_args(argv[1:])
    if not args.goals:
        print("no goals given", file=sys.stderr)
        return 1
    day = args.day.replace("-", ".") if args.day else None
    res = log_goals(args.goals, day=day)
    print(f"0g-log: +{res['added']} logged, {res['skipped']} dup ({res['day']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
