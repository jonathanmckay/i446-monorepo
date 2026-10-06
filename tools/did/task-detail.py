#!/usr/bin/env python3
"""task-detail.py <task-id> — full detail for one task, for dtd's ctrl-o preview.

dtd shows Haiku short names and its snapshot cache carries no description or
comments, so the preview pane fetches them live from Todoist (2026-10-06, user
request "I want the ability to see everything (links, description etc)").

Prints: full title, project / labels / due / priority, description, comments
(minus shorten.py's `dtd-short:` cache comments), then every link found in any
of those on its own line so the terminal can make it clickable.

Responses are cached per task for CACHE_TTL seconds so arrowing back and forth
is instant; project names are cached for a day. Never raises: fzf shows
whatever this prints, so failures print a one-line reason instead.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

_LIB = Path.home() / "i446-monorepo" / "lib"
sys.path.insert(0, str(_LIB))

CACHE_TTL = 600
PROJECT_TTL = 86400
COMMENT_SKIP = "dtd-short:"
PRIORITY = {4: "p1", 3: "p2", 2: "p3", 1: "p4"}

# Todoist markdown links [text](url), then bare URLs.
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"https?://[^\s<>()\[\]]+")

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def _cache_dir() -> Path:
    import state_paths
    d = state_paths.STATE_DIR / "task-detail"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached(path: Path, ttl: float, fetch):
    try:
        if time.time() - path.stat().st_mtime < ttl:
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    val = fetch()
    try:
        path.write_text(json.dumps(val))
    except OSError:
        pass
    return val


def links(*texts: str) -> list[str]:
    """Every URL in the given texts, in order, deduplicated."""
    out: list[str] = []
    for t in texts:
        for u in _URL.findall(t or ""):
            u = u.rstrip(".,;:!?'\"")
            if u not in out:
                out.append(u)
    return out


def demark(text: str) -> str:
    """[text](url) -> text (the URL is listed under Links instead)."""
    return _MD_LINK.sub(lambda m: m.group(1) or m.group(2), text or "")


def render(task: dict, comments: list, project: str | None) -> str:
    lines = [f"{BOLD}{demark(task.get('content', '')).strip()}{RESET}"]
    meta = []
    if project:
        meta.append(f"#{project}")
    if task.get("labels"):
        meta.append(" ".join("@" + l for l in task["labels"]))
    due = task.get("due") or {}
    if due.get("string") or due.get("date"):
        meta.append(f"due {due.get('string') or due.get('date')}")
    if task.get("priority") in (4, 3, 2):
        meta.append(PRIORITY[task["priority"]])
    if meta:
        lines.append(DIM + " · ".join(meta) + RESET)

    desc = (task.get("description") or "").strip()
    if desc:
        lines += ["", demark(desc)]

    kept = [c for c in comments
            if not (c.get("content") or "").startswith(COMMENT_SKIP)]
    if kept:
        lines += ["", f"{DIM}── comments ({len(kept)}) ──{RESET}"]
        for c in kept:
            when = (c.get("posted_at") or "")[:10]
            body = demark((c.get("content") or "").strip())
            att = (c.get("file_attachment") or c.get("attachment") or {})
            if att.get("file_url"):
                body += f"\n📎 {att.get('file_name') or 'attachment'}"
            lines.append(f"{DIM}{when}{RESET} {body}")

    urls = links(task.get("content", ""), desc,
                 *[c.get("content", "") for c in kept],
                 *[((c.get("file_attachment") or c.get("attachment") or {}).get("file_url") or "")
                   for c in kept])
    if urls:
        lines += ["", f"{DIM}── links ──{RESET}"] + urls

    if not desc and not kept and not urls:
        lines += ["", f"{DIM}(no description, comments, or links){RESET}"]
    return "\n".join(lines)


def main() -> int:
    tid = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not tid or tid.startswith("BLOCK:") or " " in tid:
        return 0  # schedule-picker row or no selection: nothing to show
    try:
        import todoist
        d = _cache_dir()
        bundle = _cached(d / f"{tid}.json", CACHE_TTL, lambda: {
            "task": todoist.get_task(tid),
            "comments": todoist.get_comments(tid),
        })
        task = bundle.get("task")
        if not task:
            print("task not found (deleted?)")
            return 0
        project = None
        pid = task.get("project_id")
        if pid:
            try:
                project = _cached(d / f"project-{pid}.json", PROJECT_TTL,
                                  lambda: (todoist._request("GET", f"/projects/{pid}") or {}).get("name"))
            except Exception:  # noqa: BLE001
                pass
        print(render(task, bundle.get("comments") or [], project))
    except Exception as e:  # noqa: BLE001
        print(f"✗ couldn't load details: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
