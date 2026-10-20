#!/usr/bin/env python3
"""book-finish — mark a `status: reading` book stub finished and bump the
weekly books-read counter in Neon (1分+1s!AM).

Usage:
  book-finish.py [title words...]

With no title: if exactly one book in the library has `status: reading`,
finish that one. With a title: match it (case-insensitive substring on the
frontmatter title) among status:reading entries.

Flips `status: reading` -> `status: finished` and adds a `finished: <date>`
line, leaving every other field (author, isbn, draft: true, etc.) untouched
so the file still works as a stub for a later `/bookreview`. Then increments
the current Sunday-anchored week's row, column AM, in the live
Neon分v12.2.xlsx `1分+1s` sheet via ix-osa.sh (the only host allowed to write
that workbook).
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys

REVIEWS = os.path.expanduser("~/vault/hcmc/reviews")
IX_OSA = os.path.expanduser("~/.claude/skills/_lib/ix-osa.sh")
SHEET = "1分+1s"
WORKBOOK = "Neon分v12.2.xlsx"
COL = "AM"

# Mirrors week_row_label() in tools/1s/1s-survey.py — keep in sync if that
# quota table ever changes.
_WEEK_QUOTA = [4, 4, 5, 4, 4, 5, 4, 4, 5, 4, 4, 5]


def _first_sunday_of_year(year: int) -> datetime.date:
    d = datetime.date(year, 1, 1)
    return d + datetime.timedelta(days=(6 - d.weekday()) % 7)


def week_row_label(sunday: datetime.date) -> str:
    year = sunday.year
    first = _first_sunday_of_year(year)
    if sunday < first:
        year -= 1
        first = _first_sunday_of_year(year)
    ordinal = (sunday - first).days // 7 + 1
    cum = 0
    for month, quota in enumerate(_WEEK_QUOTA, start=1):
        if ordinal <= cum + quota:
            return "%d.%d" % (month, ordinal - cum)
        cum += quota
    return "1.1"


def current_week_sunday(today: datetime.date) -> datetime.date:
    # weekday(): Mon=0..Sun=6
    return today - datetime.timedelta(days=(today.weekday() + 1) % 7)


def parse_frontmatter(text):
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    fm_lines = parts[1].splitlines()
    return fm_lines, parts[2]


def fm_get(fm_lines, key):
    for line in fm_lines:
        m = re.match(rf'^{key}:\s*"?(.*?)"?\s*$', line)
        if m:
            return m.group(1)
    return None


def find_book_files():
    """Yield (path, fm_lines, body) for every media:book entry."""
    for year_dir in sorted(os.listdir(REVIEWS)):
        d = os.path.join(REVIEWS, year_dir)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".md"):
                continue
            p = os.path.join(d, name)
            text = open(p, encoding="utf-8").read()
            fm_lines, body = parse_frontmatter(text)
            if fm_lines is None:
                continue
            if fm_get(fm_lines, "media") != "book":
                continue
            yield p, fm_lines, body


def run_ix_osa(script):
    proc = subprocess.run(["bash", IX_OSA], input=script, capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return False, out or err or f"ix-osa.sh exited {proc.returncode}"
    return True, out


def bump_books_read(today: datetime.date):
    label = week_row_label(current_week_sunday(today))
    script = f'''
tell application "Microsoft Excel"
    set wb to workbook "{WORKBOOK}"
    set ws to worksheet "{SHEET}" of wb
    set weekRow to 0
    set r to 2
    repeat while r < 400
        set aVal to ""
        try
            set aVal to (value of range ("A" & r) of ws) as text
        end try
        if aVal is "{label}" then
            set weekRow to r
            exit repeat
        end if
        if aVal is "" then
            exit repeat
        end if
        set r to r + 1
    end repeat
    if weekRow = 0 then return "ERROR: week {label} not found in {SHEET} col A"
    set theCell to range ("{COL}" & weekRow) of ws
    set curVal to 0
    try
        set curVal to (value of theCell) as number
    end try
    set value of theCell to curVal + 1
    return "OK: {COL}" & weekRow & "=" & (curVal + 1)
end tell
'''
    return run_ix_osa(script)


def main():
    query = " ".join(sys.argv[1:]).strip().lower()
    today = datetime.date.today()

    reading = []
    all_titles = []
    for p, fm_lines, body in find_book_files():
        status = fm_get(fm_lines, "status")
        title = fm_get(fm_lines, "title") or ""
        all_titles.append((p, title, status))
        if status == "reading":
            reading.append((p, fm_lines, body, title))

    if query:
        matches = [r for r in reading if query in r[3].lower()]
        if not matches:
            elsewhere = [t for p, t, s in all_titles if query in t.lower()]
            if elsewhere:
                sys.exit(f"NOT_READING: {elsewhere[0]!r} is in the library but not marked "
                          f"status: reading (already finished, or never started).")
            sys.exit(f"ERROR: no book matching {query!r} found in the library.")
        if len(matches) > 1:
            titles = "; ".join(m[3] for m in matches)
            sys.exit(f"AMBIGUOUS: multiple reading matches — {titles}. Be more specific.")
        path, fm_lines, body, title = matches[0]
    else:
        if not reading:
            sys.exit("ERROR: nothing currently marked status: reading. Run /book <title> first.")
        if len(reading) > 1:
            titles = "; ".join(r[3] for r in reading)
            sys.exit(f"AMBIGUOUS: multiple books in progress — {titles}. Specify a title.")
        path, fm_lines, body, title = reading[0]

    new_fm = []
    inserted_finished = False
    for line in fm_lines:
        if re.match(r"^status:\s*reading\s*$", line):
            new_fm.append("status: finished")
            new_fm.append(f"finished: {today.isoformat()}")
            inserted_finished = True
        else:
            new_fm.append(line)
    if not inserted_finished:
        sys.exit(f"ERROR: {path} had status:reading in memory but not on disk — refusing to edit "
                  f"(maybe it changed underneath us; re-run).")

    new_text = "---" + "\n".join(new_fm) + "\n---" + body
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)

    ok, msg = bump_books_read(today)
    relpath = os.path.relpath(path, os.path.expanduser("~/vault"))
    if ok:
        print(f"✓ {title} — finished → {relpath} (Neon {SHEET}!{COL} +1: {msg})")
    else:
        print(f"✓ {title} — finished → {relpath}")
        print(f"  WARN: Neon {SHEET}!{COL} increment failed: {msg}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
