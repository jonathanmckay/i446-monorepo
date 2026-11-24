"""Rows that must never reach task-queue.json, whoever writes it.

`posthoc` records (a defer's "deferred: X → date (2) [0]" stub, a split's done
portion, an ad-hoc completion) are created open and closed a few hundred ms
later, and they inherit the source task's labels (1neon, 0neon, ...). A label
or "today | overdue" fetch that lands in that window, or is served from
Todoist's lagging index, returns the stub as open, and the writers' keep-old
fallbacks then re-serve it (bug 2026-11-24: "deferred: 1 对身 11.7 (60) [30]
→ 2026-11-25 (2) [0]" listed in dtd as a task 13 s after it was closed).
No flow leaves a posthoc task open on purpose, so both cache writers
(tools/did/refresh-cache.py, did-fast.py --refresh-cache) drop them here.
"""
from __future__ import annotations

import sys

POSTHOC_LABEL = "posthoc"


def drop_posthoc(cache: dict) -> list[str]:
    """Remove posthoc-labelled rows from every list bucket, in place.

    Returns the dropped ids and names them on stderr: a stub whose close call
    failed is still open in Todoist, and this filter is now the only place
    that would otherwise notice it.
    """
    dropped: list[str] = []
    for key, rows in list(cache.items()):
        if not isinstance(rows, list):
            continue
        kept = []
        for t in rows:
            if isinstance(t, dict) and POSTHOC_LABEL in (t.get("labels") or []):
                dropped.append(str(t.get("id")))
            else:
                kept.append(t)
        cache[key] = kept
    if dropped:
        ids = ", ".join(sorted(set(dropped)))
        print(f"WARN: dropped posthoc row(s) from task queue: {ids}", file=sys.stderr)
    return dropped
