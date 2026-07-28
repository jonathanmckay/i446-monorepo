#!/usr/bin/env python3
"""points-fast.py — change a task's point value [N] from dtd.

Usage: points-fast.py <query> <new_points> <cache_file>

Resolves the task by matching the dtd row content against the cache (which
carries the Todoist id), rewrites its `[N]` in Todoist, and patches the cache
content so dtd reflects the new value on its next reload (the reload copies
$CACHE over the snapshot, so we must patch $CACHE itself).

Prints a one-line summary for the dtd header. Reuses defer-fast's Todoist API
helper for auth/transport.
"""
from __future__ import annotations  # PEP 604 `dict | None` hints on Python 3.9

import importlib.util
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("defer_fast", _HERE / "defer-fast.py")
_df = importlib.util.module_from_spec(_spec)
sys.modules["defer_fast"] = _df
_spec.loader.exec_module(_df)

_ANNOT = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")


def set_points(content: str, pts: int) -> str:
    """Replace the first [N] in content, or append [N] if none present."""
    if re.search(r"\[\d+\]", content):
        return re.sub(r"\[\d+\]", f"[{pts}]", content, count=1)
    return f"{content.rstrip()} [{pts}]"


def resolve_from_cache(cache: dict, query: str) -> dict | None:
    """Find the task dict (with id + content) matching the dtd row `query`.

    Exact content match first, then prefix (handles fzf middle-truncation '…')."""
    cands = []
    for v in cache.values():
        if isinstance(v, list):
            cands += [t for t in v if isinstance(t, dict) and t.get("id") and t.get("content")]
    q = query.strip().lower()
    for t in cands:
        if t["content"].strip().lower() == q:
            return t
    for t in cands:
        c = t["content"].strip().lower()
        if q and (c.startswith(q) or q in c):
            return t
    return None


def patch_cache(cache: dict, task_id: str, new_content: str) -> None:
    for v in cache.values():
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict) and t.get("id") == task_id:
                    t["content"] = new_content


def main() -> int:
    if len(sys.argv) < 4:
        print("✗ usage: points-fast.py <query> <new_points> <cache_file>")
        return 2
    query, pts_raw, cache_file = sys.argv[1], sys.argv[2], sys.argv[3]
    if not re.fullmatch(r"\d+", pts_raw):
        print(f"✗ not a number: {pts_raw}")
        return 1
    pts = int(pts_raw)

    try:
        cache = json.loads(Path(cache_file).read_text())
    except Exception as e:
        print(f"✗ cache unreadable: {e}")
        return 1

    task = resolve_from_cache(cache, query)
    if not task:
        print(f"✗ no task matched: {query}")
        return 1

    new_content = set_points(task["content"], pts)
    try:
        _df._api("POST", f"/tasks/{task['id']}", {"content": new_content})
    except Exception as e:
        print(f"✗ Todoist update failed: {e}")
        return 1

    patch_cache(cache, task["id"], new_content)
    try:
        Path(cache_file).write_text(json.dumps(cache))
    except Exception:
        pass  # Todoist already updated; cache will catch up on next refresh

    clean = _ANNOT.sub("", task["content"]).strip()
    print(f"✎ {clean} → [{pts}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
