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
# -1neon block-ritual cards (سمش/-1g/-1ibx) roll over every 2h block and ride in
# the cache's "today" bucket — which this lightweight refresh otherwise preserves
# verbatim. Without refetching them, the periodic daemon never surfaces a new
# block's rituals in dtd/tg-tui (regression 2026-06-29). Refetched + spliced into
# "today" in main().
RITUAL_LABEL = "-1neon"
TG_TUI_PID = Path.home() / ".cache" / "tg-tui.pid"


def _nudge_tg_tui() -> None:
    """SIGUSR1 a running tg-tui so it re-reads the freshened cache — it only
    re-reads on startup + SIGUSR1, so a silent file rewrite wouldn't show."""
    try:
        pid = int(TG_TUI_PID.read_text().strip())
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
    # Refresh the -1neon ritual subset of "today" so a new block's rituals appear
    # (the rest of "today" is left as did-fast --refresh-cache last wrote it).
    # Drop stale -1neon entries, splice in the freshly-fetched current-block cards.
    fresh_rituals = [{
        "id": t.get("id"), "content": t.get("content"), "labels": t.get("labels", []),
        "due": (t.get("due") or {}).get("date") if isinstance(t.get("due"), dict) else t.get("due"),
    } for t in todoist.find_tasks(labels=[RITUAL_LABEL], limit=200)]
    today_rest = [t for t in data.get("today", []) if RITUAL_LABEL not in t.get("labels", [])]
    data["today"] = fresh_rituals + today_rest
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
    _nudge_tg_tui()  # so a running tg-tui re-reads the freshened cache
    counts = {k: len(v) for k, v in results.items() if isinstance(v, list)}
    counts["-1neon"] = len(fresh_rituals)
    print(f"refreshed {CACHE}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
