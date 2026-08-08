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
    # `recurring` must survive shaping: dtd's daily sections show due-tomorrow
    # cards only when recurring (the 2026-06-27 drift guard); a non-recurring
    # deferred copy ("xk22 7.21") due tomorrow must stay hidden. This writer
    # runs most often and used to strip the flag, leaving that filter inert
    # (bug 2026-07-21: a deferred habit popped straight back into today).
    due = t.get("due")
    return {
        "id": t.get("id"), "content": t.get("content"), "labels": t.get("labels", []),
        "due": (due or {}).get("date") if isinstance(due, dict) else due,
        "recurring": bool((due or {}).get("is_recurring")) if isinstance(due, dict)
                     else bool(t.get("recurring")),
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
    return CACHE_KEY[label], [_shape(t) for t in raw]


def main() -> int:
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(fetch, LABELS))

    # Load the existing cache BEFORE merging results in, so the empty-fetch
    # guard below has something to fall back to.
    old_cache: dict = {}
    if CACHE.exists():
        try:
            old_cache = json.loads(CACHE.read_text())
        except (json.JSONDecodeError, OSError):
            old_cache = {}

    # Per-label empty-fetch guard (2026-08-08): find_tasks() has no retry,
    # and a bare empty/falsy result is indistinguishable from "genuinely
    # nothing open" vs. a transient Todoist rate-limit/eventual-consistency
    # hiccup on that exact label's index -- the same lag class the
    # DYNAMIC_TODAY_LABELS splice further down already guards against (see
    # its own "Per-label empty-fetch guard" comment, bug 2026-07-19). That
    # guard only ever covered the -1neon/#0g/#-1g union into "today"; this
    # base LABELS loop (关键径路/夜neon/0neon/1neon -- the bulk of dtd's
    # visible list) was never given the same protection, so a rate-limited
    # fetch here wiped a whole bucket of real, still-open tasks until the
    # next successful refresh (user report: "I'm still losing a bunch of
    # tasks after I complete one for like 10 seconds" -- Todoist's own
    # read-after-write index propagation lag, not a code-level delay).
    for lbl in LABELS:
        key = CACHE_KEY[lbl]
        if not results.get(key) and old_cache.get(key):
            results[key] = old_cache[key]

    data: dict = {"updated": datetime.now().isoformat(timespec="seconds")}
    data.update(results)
    # Preserve the "today" bucket from the existing cache if present.
    # The "today" bucket is populated by did-fast.py --refresh-cache (which
    # fetches all tasks due today/overdue). This lightweight refresh only
    # updates neon-labeled buckets and must not drop the broader task list.
    if "today" in old_cache and "today" not in data:
        data["today"] = old_cache["today"]
    # Refresh the dynamic subset of "today" (rituals + #0g/#-1g goals) so newly-set
    # goals and a new block's rituals appear (the rest of "today" is left as
    # did-fast --refresh-cache last wrote it). Drop stale entries for these labels,
    # splice in the freshly-fetched ones, dedup by id (a task may carry two).
    #
    # Per-label empty-fetch guard (bug 2026-07-19: "-1n tasks disappear ... for
    # like 5 seconds" whenever a ritual is completed). find_tasks() has no retry
    # and a bare empty list is indistinguishable from "genuinely nothing open" vs.
    # a transient Todoist eventual-consistency hiccup on that exact label's index
    # (the same lag class did-fast.py's own fetch_today already guards against
    # with a retry + fallback-to-old). Completing a ritual writes to the SAME
    # -1neon label being queried here, and this runs both on a launchd timer and
    # fire-and-forget after every write, so the race is frequent in practice. If a
    # label's fresh fetch comes back empty but the previous cache had entries
    # under it, trust the old data instead of wiping still-open cards — the
    # unconditional splice below otherwise drops them from "today" outright, not
    # just leaves them un-refreshed.
    # Partial-fetch erosion guard (2026-07-28): under a Todoist 5xx/rate storm
    # the label index can return a strict SUBSET with a 200 — the empty-only
    # guard below passes it through, and the splice then REPLACES the block's
    # full ritual set with the subset. Each refresh eroded further, down to
    # did-fast's "carrying 1 cached card(s)" (user report: "-1n tasks
    # disappeared from dtd after completing a task"). Union the old cache's
    # entries for the label back in (dedup by id), pruning ids recorded closed
    # in completed-today (run_ritual and regular closes record them) — but
    # only while the old cache was written in the SAME 2h block, so cards the
    # daemon retired at a boundary never outlive their block.
    _now = datetime.now()
    try:
        _upd = datetime.fromisoformat(old_cache.get("updated", ""))
        same_block = (_upd.date() == _now.date()
                      and _upd.hour // 2 == _now.hour // 2)
    except (ValueError, TypeError):
        same_block = False
    closed_ids: set = set()
    if same_block:
        try:
            _ct = json.loads(_sp.COMPLETED_TODAY.read_text())
            if _ct.get("date") == _now.strftime("%Y-%m-%d"):
                closed_ids = {str(v) for v in (_ct.get("ids") or {}).values()}
        except Exception:
            pass
    fresh_dynamic, _seen = [], set()
    for lbl in DYNAMIC_TODAY_LABELS:
        fetched = todoist.find_tasks(labels=[lbl], limit=200)
        if not fetched:
            fetched = [t for t in data.get("today", []) if lbl in t.get("labels", [])]
        if same_block:
            have = {t.get("id") for t in fetched}
            fetched = fetched + [t for t in data.get("today", [])
                                 if lbl in t.get("labels", [])
                                 and t.get("id") not in have
                                 and str(t.get("id")) not in closed_ids]
        for t in fetched:
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
    # Atomic write (tmp + rename): dtd's auto-reload watcher polls this file's
    # mtime every 2s and its list-generator does an unguarded json.load() with
    # no retry. A plain write_text() truncates the file before writing the new
    # content, so a poll landing in that window reads a truncated/empty file,
    # the generator crashes on the parse, and dtd's list goes blank until the
    # next successful poll (up to another 2s) -- the "flashes then goes blank
    # for a full 2s" bug (2026-08-07). did-fast.py's own cache writer already
    # uses this exact tmp+rename pattern for the same file; this writer never
    # got it.
    tmp_path = CACHE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp_path.rename(CACHE)
    _nudge_janus()  # so a running janus re-reads the freshened cache
    counts = {k: len(v) for k, v in results.items() if isinstance(v, list)}
    counts["today-dynamic"] = len(fresh_dynamic)
    print(f"refreshed {CACHE}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
