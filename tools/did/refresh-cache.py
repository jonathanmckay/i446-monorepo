#!/usr/bin/env python3
"""
Refresh ~/vault/z_ibx/task-queue.json from Todoist.

Fetches all open tasks for the four label buckets (0neon, 1neon, 关键径路, 夜neon)
in parallel, writes a fresh cache. Designed to run:

  - on a launchd timer every ~5 minutes (so /next stays fresh)
  - fire-and-forget after each /did write (so just-completed tasks vanish)

Idempotent. Safe to run concurrently (last writer wins; both write same data).
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

import todoist  # noqa: E402

CACHE = Path.home() / "vault" / "z_ibx" / "task-queue.json"
LABELS = ["关键径路", "夜neon", "0neon", "1neon"]
CACHE_KEY = {"关键径路": "关键路径", "夜neon": "夜neon", "0neon": "0neon", "1neon": "1neon"}


def fetch(label: str) -> tuple[str, list]:
    """Fetch all open tasks for a label, project-shaped for the cache."""
    raw = todoist.find_tasks(labels=[label], limit=200)
    out = []
    for t in raw:
        out.append({
            "id": t.get("id"),
            "content": t.get("content"),
            "labels": t.get("labels", []),
            "due": (t.get("due") or {}).get("date") if isinstance(t.get("due"), dict) else t.get("due"),
        })
    return CACHE_KEY[label], out


def main() -> int:
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(fetch, LABELS))
    data: dict = {"updated": datetime.now().isoformat(timespec="seconds")}
    data.update(results)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    counts = {k: len(v) for k, v in results.items()}
    print(f"refreshed {CACHE}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
