#!/usr/bin/env python3
"""Shorten long task titles for the dtd / next pickers.

Long m5x2-style task names get middle-truncated by fzf, which eats the trailing
(N) time / [N] points estimates. This module produces a compact display name
(<=PROSE_CAP chars of prose) with the original estimate tokens re-appended, so
the estimates always survive.

The short name is generated ONCE per task by a cheap model (Haiku) and cached in
two places:
  - a local sidecar (~/vault/z_ibx/task-shortnames.json) — fast path, keyed by
    task id + a hash of the original content (so edits regenerate)
  - a Todoist comment (`dtd-short:<hash8>:<short>`) — durable, portable across
    devices, and the thing the user asked to "store in a comment"

Everything is best-effort: any failure (no API key, network, Todoist) returns
None for that task and the caller falls back to the full content. It must never
break the cache refresh.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

PROSE_CAP = 32  # max chars of prose in the shortened name (estimates appended on top)
SIDECAR = Path.home() / "vault" / "z_ibx" / "task-shortnames.json"
MODEL = "claude-haiku-4-5-20251001"
COMMENT_PREFIX = "dtd-short:"

# Estimate tokens: (30) time, [45] / [0G] points, {15} 0g bonus, ((10)) nested.
# Require a digit (or the G flag) so path-like "(h335/i9)" is NOT treated as one.
_EST = re.compile(r"\(\(?\d+\)?\)|\[\d*G?\]|\{\d+\}")


def split_estimates(content: str) -> tuple[str, str]:
    """Return (bare_prose, estimates) where estimates is the original tokens
    joined by spaces in their original order. Pure + side-effect free → unit
    tested."""
    tokens = [m.group(0) for m in _EST.finditer(content)]
    prose = _EST.sub("", content)
    prose = re.sub(r"\s+", " ", prose).strip(" -–—:")
    return prose, " ".join(tokens)


def _hash(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()[:8]


def _load_sidecar() -> dict:
    try:
        return json.loads(SIDECAR.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_sidecar(data: dict) -> None:
    try:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        tmp = SIDECAR.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(SIDECAR)
    except OSError:
        pass


def _haiku_shorten(prose: str) -> str | None:
    """Ask Haiku for a <=PROSE_CAP-char version of the prose. Returns None on
    any failure."""
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=40,
            messages=[{
                "role": "user",
                "content": (
                    f"Shorten this task title to at most {PROSE_CAP} characters. "
                    "Keep the key noun, proper names, and codes (e.g. m5x2, LX, R202). "
                    "Drop filler words and parenthetical paths. No trailing punctuation. "
                    "Return ONLY the shortened title, nothing else.\n\n"
                    f"Title: {prose}"
                ),
            }],
        )
        short = resp.content[0].text.strip().strip('"').strip()
        short = re.sub(r"\s+", " ", short)
        if not short:
            return None
        # Cap in case the model overshoots — prefer a word boundary so we never
        # leave a chopped fragment like "scop".
        if len(short) > PROSE_CAP:
            cut = short[:PROSE_CAP]
            sp = cut.rfind(" ")
            short = (cut[:sp] if sp >= PROSE_CAP - 12 else cut).rstrip(" -–—:,")
        return short
    except Exception:
        return None


def _comment_lookup(task_id: str, want_hash: str):
    """Return a cached short name from the task's Todoist comments if one exists
    for this content hash. Best-effort."""
    try:
        sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
        import todoist  # noqa
        for c in todoist.get_comments(task_id):
            body = (c.get("content") or "").strip()
            if body.startswith(COMMENT_PREFIX):
                rest = body[len(COMMENT_PREFIX):]
                h, _, short = rest.partition(":")
                if h == want_hash and short:
                    return short
    except Exception:
        return None
    return None


def _comment_store(task_id: str, content_hash: str, short: str) -> None:
    try:
        sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
        import todoist  # noqa
        todoist.add_comment(task_id, f"{COMMENT_PREFIX}{content_hash}:{short}")
    except Exception:
        pass


def shorten_tasks(tasks: list[dict], max_new: int = 12) -> dict:
    """Given task dicts (need 'id' and 'content'), return {id: short_display} for
    every task whose prose exceeds PROSE_CAP. Reuses cached names; only calls
    Haiku for genuinely new long tasks, at most `max_new` per run so a big first
    batch never blocks a post-/did refresh (the rest fill in on later runs).
    Never raises."""
    from concurrent.futures import ThreadPoolExecutor

    sidecar = _load_sidecar()
    out: dict = {}
    dirty = False
    candidates = []  # (tid, prose, est, hash) needing a network round trip

    for t in tasks:
        tid = str(t.get("id") or "")
        content = t.get("content") or ""
        if not tid or not content:
            continue
        prose, est = split_estimates(content)
        if len(prose) <= PROSE_CAP:
            continue  # short enough already

        h = _hash(content)
        cached = sidecar.get(tid)
        if cached and cached.get("h") == h and cached.get("short"):
            out[tid] = cached["short"]  # fast path, no network
        else:
            candidates.append((tid, prose, est, h))

    def resolve(c):
        """Return (tid, hash, display, generated) or None. Runs in a thread."""
        tid, prose, est, h = c
        # The comment stores the FULL display (prose + estimates already joined).
        # Use it verbatim — do NOT re-append `est` or the estimates double up.
        cached_display = _comment_lookup(tid, h)
        if cached_display:
            return tid, h, cached_display, False
        short_prose = _haiku_shorten(prose)
        if not short_prose:
            return None
        display = f"{short_prose} {est}".strip() if est else short_prose
        return tid, h, display, True

    # Bound generation per run so a big first batch never stalls a post-/did
    # refresh; the rest fill in on later cycles. Run the network work in parallel.
    to_do = candidates[:max_new]
    if to_do:
        with ThreadPoolExecutor(max_workers=min(6, len(to_do))) as pool:
            for res in pool.map(resolve, to_do):
                if not res:
                    continue
                tid, h, display, generated = res
                out[tid] = display
                sidecar[tid] = {"h": h, "short": display}
                dirty = True
                if generated:
                    _comment_store(tid, h, display)

    # NB: do NOT prune sidecar entries for ids absent from `tasks`. The cache is
    # rebuilt by two callers with DIFFERENT task sets (did-fast's "today" bucket
    # vs refresh-cache's neon buckets); pruning on a partial view makes them
    # delete each other's entries, forcing constant regeneration. The sidecar is
    # one tiny line per long task, so unbounded growth is a non-issue.
    if dirty:
        _save_sidecar(sidecar)
    return out


def attach_to_cache(cache: dict) -> dict:
    """Mutate a task-queue cache dict in place, adding a 'short' field to every
    long task across all list buckets. Call this from EVERY path that rebuilds
    the cache (refresh-cache.py and did-fast.py --refresh-cache) so a refresh
    never drops the short names. Best-effort: never raises."""
    try:
        all_tasks, seen = [], set()
        for v in cache.values():
            if isinstance(v, list):
                for t in v:
                    tid = t.get("id") if isinstance(t, dict) else None
                    if tid and tid not in seen:
                        seen.add(tid)
                        all_tasks.append(t)
        shorts = shorten_tasks(all_tasks)
        for v in cache.values():
            if isinstance(v, list):
                for t in v:
                    if isinstance(t, dict):
                        s = shorts.get(t.get("id"))
                        if s:
                            t["short"] = s
                        else:
                            t.pop("short", None)  # drop stale short if no longer long
    except Exception as e:
        print(f"shorten attach skipped: {e}", file=sys.stderr)
    return cache


if __name__ == "__main__":
    # Smoke test: echo shortenings for tasks piped in, or run split_estimates demo.
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        for s in [
            "Continue automating Andy's leasing stat sheet — Phase 2 (30) [45]",
            "Reconnect QBO OAuth — unblocks m5x2 debt schedules",
            "LP scorecard: define 5 maintenance metrics (15) [30]",
            "150 pts ((10)) {15}",
            "Fill LX m5x2 roles & expectations doc [20] (20)",
        ]:
            prose, est = split_estimates(s)
            print(f"{len(prose):>3}  prose={prose!r}  est={est!r}")
    else:
        data = json.load(sys.stdin)
        tasks = data if isinstance(data, list) else data.get("tasks", [])
        print(json.dumps(shorten_tasks(tasks), ensure_ascii=False, indent=2))
