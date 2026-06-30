"""Cross-link daily build-order archives.

Each day's build order is snapshotted to vault/g245/v_logs/<YYYY.MM.DD>-build-order.md
by -2n.py (snapshot_build_order) and -1g-cron.py (_archive_before_reset). This
helper inserts a wikilink to the PREVIOUS day's archive just under the
frontmatter, so the archives are navigable backward in Obsidian.
"""
from __future__ import annotations

import datetime as _dt
import re

_LINK_RE = re.compile(r"\[\[\d{4}\.\d{2}\.\d{2}-build-order")


def prev_day_link_line(archived_date: _dt.date) -> str:
    """The nav line for the archive representing `archived_date`."""
    prev = (archived_date - _dt.timedelta(days=1)).strftime("%Y.%m.%d")
    return f"◀ Previous: [[{prev}-build-order|{prev}]]"


def with_prev_day_link(text: str, archived_date: _dt.date) -> str:
    """Return `text` with a previous-day nav link inserted after the frontmatter
    (or prepended if there's none). Idempotent: if a build-order back-link is
    already present, return the text unchanged."""
    if _LINK_RE.search(text):
        return text
    line = prev_day_link_line(archived_date)
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":  # closing frontmatter fence
                lines[i + 1:i + 1] = ["", line]
                return "\n".join(lines)
    return line + "\n\n" + text
