#!/usr/bin/env python3
"""book-finish — mark a `status: reading` book stub finished and bump the
weekly books-read counter in Neon (1分+1s!AM).

Usage:
  book-finish.py [title words...]
  book-finish.py --reviewed --file <review path>

`--reviewed --file` is the /bookreview PUBLISH path (JM 2026-11-22: publishing
a review marks the book finished). It takes the exact review file, whatever
its status, sets `status: finished` and `finished: <the review's date:>`, and
bumps Neon only when that date falls in the current week and the book was not
already finished under another file (a /book stub by the same author).

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

sys.path.insert(0, os.path.expanduser("~/i446-monorepo/lib"))
from neon import excel as neon_excel  # noqa: E402  excel-http daemon client (journaled writes)

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
    # Daemon first: mode "add" is the same read-modify-write inside ONE
    # osascript, journaled (Neon-off-Excel step 2c, 2026-10-19). The week
    # label resolves through the daemon's 1分+1s col-A lookup.
    try:
        r = neon_excel.batch_write(SHEET, [{"col": COL, "value": 1, "mode": "add"}],
                                   date=label, src="book finished", fallback=False)
        if r.get("ok"):
            res = (r.get("results") or [{}])[0]
            return True, f"OK: {COL}{r.get('row')}={res.get('value')}"
        if not r.get("fallback"):
            return False, f"daemon: {r.get('error') or r}"
    except Exception as e:  # noqa: BLE001
        print(f"book-finish daemon path failed ({e}); using ix-osa", file=sys.stderr)
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


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def find_finished_twin(path, title, author):
    """A different file for the same book that is already finished: same
    author, and its title appears inside this review's title (a /book stub is
    'Book Title'; a review is 'Punchy Line: Book Title'). Returns
    (path, finished) or None."""
    title_l, author_l = (title or "").lower(), (author or "").strip().lower()
    if not author_l:
        return None
    for p, fm_lines, _body in find_book_files():
        if os.path.abspath(p) == os.path.abspath(path):
            continue
        if fm_get(fm_lines, "status") != "finished":
            continue
        if (fm_get(fm_lines, "author") or "").strip().lower() != author_l:
            continue
        other = (fm_get(fm_lines, "title") or "").lower()
        if other and other in title_l:
            return p, fm_get(fm_lines, "finished")
    return None


def finish_reviewed(path, today):
    """Mark the review at `path` finished. Returns (exit code, message)."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        return 1, f"ERROR: no such file: {path}"
    fm_lines, body = parse_frontmatter(open(path, encoding="utf-8").read())
    if fm_lines is None:
        return 1, f"ERROR: {path} has no frontmatter."
    if fm_get(fm_lines, "media") != "book" or fm_get(fm_lines, "type") != "review":
        return 1, f"ERROR: {path} is not a book review (needs media: book and type: review)."
    title = fm_get(fm_lines, "title") or os.path.basename(path)
    if fm_get(fm_lines, "status") == "finished":
        return 0, f"ALREADY_FINISHED: {title}"

    twin = find_finished_twin(path, title, fm_get(fm_lines, "author"))
    finished = (_parse_date(twin[1]) if twin else None) or _parse_date(fm_get(fm_lines, "date")) or today

    new_fm, saw_status, saw_finished = [], False, False
    for line in fm_lines:
        if re.match(r"^status:", line):
            new_fm.append("status: finished")
            saw_status = True
        elif re.match(r"^finished:", line):
            new_fm.append(f"finished: {finished.isoformat()}")
            saw_finished = True
        else:
            new_fm.append(line)
    while new_fm and not new_fm[-1].strip():
        new_fm.pop()
    if not saw_status:
        new_fm.append("status: finished")
    if not saw_finished:
        new_fm.append(f"finished: {finished.isoformat()}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("---" + "\n".join(new_fm) + "\n---" + body)

    relpath = os.path.relpath(path, os.path.expanduser("~/vault"))
    if twin:
        return 0, (f"ALREADY_FINISHED: {title} was counted under "
                   f"{os.path.relpath(twin[0], os.path.expanduser('~/vault'))}; "
                   f"marked {relpath} finished {finished.isoformat()}, Neon not bumped")
    if current_week_sunday(finished) != current_week_sunday(today):
        return 0, (f"✓ {title} — finished {finished.isoformat()} → {relpath} "
                   f"(Neon not bumped: not this week)")
    ok, msg = bump_books_read(today)
    if ok:
        return 0, f"✓ {title} — finished {finished.isoformat()} → {relpath} (Neon {SHEET}!{COL} +1: {msg})"
    print(f"  WARN: Neon {SHEET}!{COL} increment failed: {msg}", file=sys.stderr)
    return 0, f"✓ {title} — finished {finished.isoformat()} → {relpath}"


def main():
    args = sys.argv[1:]
    if "--reviewed" in args or "--file" in args:
        if "--reviewed" not in args or "--file" not in args or args.index("--file") + 1 >= len(args):
            sys.exit("ERROR: use --reviewed --file <review path> together.")
        code, msg = finish_reviewed(args[args.index("--file") + 1], datetime.date.today())
        print(msg, file=sys.stderr if code else sys.stdout)
        return code

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
