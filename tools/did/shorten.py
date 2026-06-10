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
        # Hard cap in case the model overshoots.
        if len(short) > PROSE_CAP:
            short = short[:PROSE_CAP].rstrip()
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


def shorten_tasks(tasks: list[dict]) -> dict:
    """Given task dicts (need 'id' and 'content'), return {id: short_display} for
    every task whose prose exceeds PROSE_CAP. Reuses cached names; only calls
    Haiku for genuinely new long tasks. Never raises."""
    sidecar = _load_sidecar()
    out: dict = {}
    dirty = False
    seen_ids = set()

    for t in tasks:
        tid = str(t.get("id") or "")
        content = t.get("content") or ""
        if not tid or not content:
            continue
        seen_ids.add(tid)
        prose, est = split_estimates(content)
        if len(prose) <= PROSE_CAP:
            continue  # short enough already

        h = _hash(content)
        cached = sidecar.get(tid)
        if cached and cached.get("h") == h and cached.get("short"):
            out[tid] = cached["short"]
            continue

        # Sidecar miss → check the durable comment (cross-device), else generate.
        short_prose = _comment_lookup(tid, h)
        generated = False
        if not short_prose:
            short_prose = _haiku_shorten(prose)
            generated = True
        if not short_prose:
            continue  # give up quietly; caller falls back to full content

        display = f"{short_prose} {est}".strip() if est else short_prose
        out[tid] = display
        sidecar[tid] = {"h": h, "short": display}
        dirty = True
        if generated:
            _comment_store(tid, h, display)

    # Prune sidecar entries for tasks no longer present (keep it from growing
    # unbounded). Only prune when we actually saw a task list.
    if seen_ids:
        for stale in [k for k in sidecar if k not in seen_ids]:
            del sidecar[stale]
            dirty = True

    if dirty:
        _save_sidecar(sidecar)
    return out


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
