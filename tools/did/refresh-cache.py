#!/usr/bin/env python3
"""
Refresh ~/.local/state/jm/task-queue.json from Todoist.

Fetches all open tasks for the four label buckets (0neon, 1neon, 关键径路, 夜neon)
in parallel, writes a fresh cache. Designed to run:

  - on a launchd timer every ~5 minutes (so /next stays fresh)
  - fire-and-forget after each /did write (so just-completed tasks vanish)

Idempotent. Safe to run concurrently (last writer wins; both write same data).
"""

from __future__ import annotations

import json
import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

import todoist  # noqa: E402

import sys as _sys; _sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib")); import state_paths as _sp
CACHE = _sp.TASK_QUEUE
LABELS = ["关键径路", "夜neon", "0neon", "1neon"]
CACHE_KEY = {"关键径路": "关键路径", "夜neon": "夜neon", "0neon": "0neon", "1neon": "1neon"}
# Dynamic "today"-bucket labels that change intra-day and must be refetched every
# run. The "today" bucket is otherwise preserved verbatim, so without this the
# periodic daemon never surfaces newly-set goals or a new block's rituals in
# dtd/janus — and the skills' background `--refresh-cache &` doesn't reliably
# complete, so the daemon is the dependable path (regression 2026-06-29/30):
#   -1neon  block rituals (سمش/-1g/-1ibx), roll over every 2h block
#   #0g     daily goals set via /0g
#   #-1g    block goals set via /-1g
DYNAMIC_TODAY_LABELS = ["-1neon", "#0g", "#-1g"]
JANUS_PID = Path.home() / ".cache" / "janus.pid"


def _shape(t: dict) -> dict:
    return {
        "id": t.get("id"), "content": t.get("content"), "labels": t.get("labels", []),
        "due": (t.get("due") or {}).get("date") if isinstance(t.get("due"), dict) else t.get("due"),
    }


def _nudge_janus() -> None:
    """SIGUSR1 a running janus so it re-reads the freshened cache — it only
    re-reads on startup + SIGUSR1, so a silent file rewrite wouldn't show."""
    try:
        pid = int(JANUS_PID.read_text().strip())
        os.kill(pid, signal.SIGUSR1)
    except (OSError, ValueError):
        pass


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
    # Preserve the "today" bucket from the existing cache if present.
    # The "today" bucket is populated by did-fast.py --refresh-cache (which
    # fetches all tasks due today/overdue). This lightweight refresh only
    # updates neon-labeled buckets and must not drop the broader task list.
    if CACHE.exists():
        try:
            old = json.loads(CACHE.read_text())
            if "today" in old and "today" not in data:
                data["today"] = old["today"]
        except (json.JSONDecodeError, OSError):
            pass
    # Refresh the dynamic subset of "today" (rituals + #0g/#-1g goals) so newly-set
    # goals and a new block's rituals appear (the rest of "today" is left as
    # did-fast --refresh-cache last wrote it). Drop stale entries for these labels,
    # splice in the freshly-fetched ones, dedup by id (a task may carry two).
    fresh_dynamic, _seen = [], set()
    for lbl in DYNAMIC_TODAY_LABELS:
        for t in todoist.find_tasks(labels=[lbl], limit=200):
            if t.get("id") not in _seen:
                _seen.add(t.get("id"))
                fresh_dynamic.append(_shape(t))
    today_rest = [t for t in data.get("today", [])
                  if not any(l in t.get("labels", []) for l in DYNAMIC_TODAY_LABELS)]
    data["today"] = fresh_dynamic + today_rest
    # Attach short display names (Haiku, cached once per task) so the pickers can
    # show long m5x2-style tasks without fzf eating the (N)/[N] estimates.
    # Shared with did-fast.py's --refresh-cache so every refresh path agrees.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import shorten  # noqa: E402
        shorten.attach_to_cache(data)
    except Exception as e:
        print(f"shorten skipped: {e}", file=sys.stderr)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    _nudge_janus()  # so a running janus re-reads the freshened cache
    counts = {k: len(v) for k, v in results.items() if isinstance(v, list)}
    counts["today-dynamic"] = len(fresh_dynamic)
    print(f"refreshed {CACHE}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
