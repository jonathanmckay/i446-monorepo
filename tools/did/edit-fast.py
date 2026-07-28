#!/usr/bin/env python3
"""edit-fast.py — unified task edit for dtd's ctrl-g.

Usage: edit-fast.py <query> <edit_string> <cache_file>

One prompt edits three things on the highlighted task from a single line:
  - any @code token       → domain label   (swap; mirrors domain-fast)
  - any standalone integer → points [N]     (last number wins)
  - all remaining text     → new task name  (trailing annotations preserved)

Resolves the task from the dtd snapshot cache, applies the changes in Todoist
(content for name/points, labels for domain), patches the cache so the row
updates on reload, and prints a one-line summary for the dtd header. Reuses
domain-fast (DOMAINS/swap_domain) and points-fast (set_points/resolve/_api).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).parent


def _load(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, _HERE / filename)
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = m
    spec.loader.exec_module(m)
    return m


_dom = _load("domain_fast", "domain-fast.py")  # DOMAINS, swap_domain, patch_cache_labels
_pf = _load("points_fast", "points-fast.py")   # set_points, resolve_from_cache, patch_cache
_df = _pf._df                                   # Todoist _api transport
_short = _load("shorten_mod", "shorten.py")     # shorten_tasks — same fn the cache refresh uses

DOMAINS = _dom.DOMAINS

# One or more trailing (time)/[pts]/{bonus} annotation groups at end of content.
_TRAIL_ANNOT = re.compile(r"(?:\s*[\(\[\{][^\)\]\}]*[\)\]\}])+\s*$")
# A single annotation token of any bracket kind, anywhere.
_ANNOT_TOK = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")


def set_name(content: str, new_name: str) -> str:
    """Replace the leading name, preserving trailing (time)/[pts]/{bonus}
    annotations — EXCEPT the bracket kinds the new name itself carries, which
    would otherwise duplicate (bug 2026-07-24: retyping the full line
    "…photos (15) [20]" over "…photos (15) [15]" produced "(15) [20] (15)
    [15]" — the typed annotations landed in the name and the old tail was
    appended after them)."""
    m = _TRAIL_ANNOT.search(content)
    tail = content[m.start():].strip() if m else ""
    base = new_name.strip()
    kinds = {t[0] for t in _ANNOT_TOK.findall(base)}
    if kinds and tail:
        tail = " ".join(t for t in _ANNOT_TOK.findall(tail) if t[0] not in kinds)
    return (base + (" " + tail if tail else "")).strip()


def _resync_short(cache: dict, task_id: str, new_content: str) -> None:
    """Drop the cached Haiku `short` name for the OLD content, then
    regenerate one for the NEW content immediately (via the same
    shorten_tasks() the periodic cache refresh uses) if it's long enough to
    need one. Without this, a rename that crosses PROSE_CAP just fell back to
    fzf's raw middle-truncation — eating the trailing (N)/[N] estimate —
    until whenever the next full --refresh-cache happened to run (bug
    2026-07-28: an edited task stayed unshortened, unlike every other long
    task, which gets shortened by that same refresh pass)."""
    short = _short.shorten_tasks([{"id": task_id, "content": new_content}]).get(task_id)
    for v in cache.values():
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict) and t.get("id") == task_id:
                    if short:
                        t["short"] = short
                    else:
                        t.pop("short", None)


def parse_edits(edit_string: str) -> tuple[str | None, str | None, int | None]:
    """Parse a ctrl-g edit line into (new_name, domain, points).

    @code → domain; a standalone integer → points (last one wins, earlier
    numbers fall back into the name); everything else → name. Any field is
    None when absent."""
    domain: str | None = None
    points: int | None = None
    name_tokens: list[str] = []
    for tok in edit_string.split():
        if tok.startswith("@") and len(tok) > 1:
            domain = tok[1:]
        elif re.fullmatch(r"\d+", tok):
            if points is not None:
                name_tokens.append(str(points))  # demote the earlier number to name
            points = int(tok)
        elif re.fullmatch(r"\[\d+\]", tok):
            # "[20]" typed in display syntax means points, not name text —
            # as a name token it duplicated next to the preserved [N] tail
            # (bug 2026-07-24: "changed points and it doubled up").
            if points is not None:
                name_tokens.append(str(points))
            points = int(tok[1:-1])
        else:
            name_tokens.append(tok)
    new_name = " ".join(name_tokens).strip() or None
    return new_name, domain, points


def main() -> int:
    # Forms:  edit-fast.py --id <id> <edits> <cache_file>   ← dtd
    #         edit-fast.py <query>   <edits> <cache_file>   ← fallback
    argv = sys.argv[1:]
    task_id = None
    if argv and argv[0] == "--id":
        task_id = argv[1] if len(argv) > 1 else None
        argv = argv[2:]
    if (task_id is None and len(argv) < 3) or (task_id is not None and len(argv) < 2):
        print("✗ usage: edit-fast.py [--id <id>] <query> <edits> <cache_file>")
        return 2
    if task_id is not None:
        query, edits, cache_file = None, argv[0], argv[1]
    else:
        query, edits, cache_file = argv[0], argv[1], argv[2]
    new_name, domain, points = parse_edits(edits)
    if new_name is None and domain is None and points is None:
        print("✗ nothing to change")
        return 1
    if domain is not None and domain not in DOMAINS:
        print(f"✗ unknown domain: {domain}")
        return 1

    try:
        cache = json.loads(Path(cache_file).read_text())
    except Exception as e:
        print(f"✗ cache unreadable: {e}")
        return 1

    task = _pf.resolve_by_id(cache, task_id) if task_id else _pf.resolve_from_cache(cache, query)
    if not task:
        print(f"✗ no task matched: {task_id or query}")
        return 1

    orig_content = task["content"]
    orig_clean = _pf._ANNOT.sub("", orig_content).strip()
    summary: list[str] = []

    # 1. content (name and/or points) — one Todoist call
    new_content = orig_content
    if new_name is not None:
        new_content = set_name(new_content, new_name)
    if points is not None:
        new_content = _pf.set_points(new_content, points)
    if new_content != orig_content:
        try:
            _df._api("POST", f"/tasks/{task['id']}", {"content": new_content})
        except Exception as e:
            print(f"✗ Todoist content update failed: {e}")
            return 1
        _pf.patch_cache(cache, task["id"], new_content)
        if new_name is not None:
            _resync_short(cache, task["id"], new_content)  # regenerate for the NEW content, don't just drop
            summary.append(f'name→"{new_name}"')
        if points is not None:
            summary.append(f"[{points}]")

    # 2. domain (labels) — separate Todoist call
    if domain is not None:
        old = [l for l in task.get("labels", []) if l in DOMAINS]
        new_labels = _dom.swap_domain(task.get("labels", []), domain)
        try:
            _df._api("POST", f"/tasks/{task['id']}", {"labels": new_labels})
        except Exception as e:
            print(f"✗ Todoist domain update failed: {e}")
            return 1
        _dom.patch_cache_labels(cache, task["id"], new_labels)
        summary.append(f"{old[0]}→{domain}" if old else f"@{domain}")

    try:
        Path(cache_file).write_text(json.dumps(cache))
    except Exception:
        pass  # Todoist already updated; cache catches up on next refresh

    if not summary:
        print(f"✎ {orig_clean}: no change")
    else:
        print(f"✎ {orig_clean}: " + " · ".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
