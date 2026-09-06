#!/usr/bin/env python3
"""book-week-report — list books started/finished within a date range.

Usage:
  book-week-report.py <week_start ISO> <week_end ISO>

Scans ~/vault/hcmc/reviews/<year>/*.md for media:book entries and buckets
each by its `date:` (started, set once at /book creation time and never
touched again) and `finished:` (set by /book finished) frontmatter fields,
inclusive of both endpoints. Prints:

  STARTED\t<title>\t<author>
  FINISHED\t<title>\t<author>

one line per hit, for the caller to format.
"""

from __future__ import annotations

import datetime
import os
import re
import sys

REVIEWS = os.path.expanduser("~/vault/hcmc/reviews")


def parse_frontmatter(text):
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    return parts[1].splitlines()


def fm_get(fm_lines, key):
    for line in fm_lines:
        m = re.match(rf'^{key}:\s*"?(.*?)"?\s*$', line)
        if m:
            return m.group(1)
    return None


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s.strip())
    except ValueError:
        return None


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: book-week-report.py <week_start ISO> <week_end ISO>")
    week_start = datetime.date.fromisoformat(sys.argv[1])
    week_end = datetime.date.fromisoformat(sys.argv[2])

    for year_dir in sorted(os.listdir(REVIEWS)):
        d = os.path.join(REVIEWS, year_dir)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".md"):
                continue
            text = open(os.path.join(d, name), encoding="utf-8").read()
            fm_lines = parse_frontmatter(text)
            if fm_lines is None or fm_get(fm_lines, "media") != "book":
                continue
            title = fm_get(fm_lines, "title") or name
            author = fm_get(fm_lines, "author") or ""
            started = parse_date(fm_get(fm_lines, "date"))
            finished = parse_date(fm_get(fm_lines, "finished"))
            if started and week_start <= started <= week_end:
                print(f"STARTED\t{title}\t{author}")
            if finished and week_start <= finished <= week_end:
                print(f"FINISHED\t{title}\t{author}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
