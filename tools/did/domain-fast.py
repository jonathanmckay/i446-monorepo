#!/usr/bin/env python3
"""domain-fast.py — change a task's domain label from dtd (ctrl-g).

Usage: domain-fast.py <query> <new_domain> <cache_file>

Resolves the task by matching the dtd row content against the snapshot cache
(which carries the Todoist id + labels), swaps its domain label in Todoist
(removes any existing domain label, adds <new_domain>), and patches the cache
labels so the row recolors on dtd's next reload.

The domain set mirrors dtd.sh's COLORS palette: the labels that map to a row
color and (via tg-fast --resolve) to a Toggl project. Bookkeeping labels
(posthoc, section tags like 0neon, #0g …) are preserved untouched.

Prints a one-line summary for the dtd header. Reuses defer-fast's Todoist API
helper for auth/transport and points-fast's cache resolver.
"""
from __future__ import annotations  # PEP 604 `list | None` hints on Python 3.9

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


_df = _load("defer_fast", "defer-fast.py")    # _api transport
_pf = _load("points_fast", "points-fast.py")  # resolve_from_cache

# Canonical domain labels — mirrors dtd.sh's COLORS keys (label → row color /
# Toggl project). Exactly one of these should be on a task at a time.
DOMAINS = {
    "g245", "epcn", "s897", "hcmc2", "xk87", "xk88", "hci", "i9", "n156",
    "hcmc", "m5x2", "hcb", "hcbp", "infra", "i444", "i447", "hcm", "hcmp",
    "hcmr", "家", "睡觉",
}

_ANNOT = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")


def swap_domain(labels: list, new_domain: str) -> list:
    """Drop any existing domain label(s), append the new one. Non-domain
    bookkeeping labels keep their order so sections/#0g/posthoc survive."""
    kept = [l for l in labels if l not in DOMAINS]
    return kept + [new_domain]


def patch_cache_labels(cache: dict, task_id: str, new_labels: list) -> None:
    for v in cache.values():
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict) and t.get("id") == task_id:
                    t["labels"] = new_labels


def main() -> int:
    if len(sys.argv) < 4:
        print("✗ usage: domain-fast.py <query> <new_domain> <cache_file>")
        return 2
    query, domain, cache_file = sys.argv[1], sys.argv[2].strip(), sys.argv[3]
    if domain not in DOMAINS:
        print(f"✗ unknown domain: {domain}")
        return 1

    try:
        cache = json.loads(Path(cache_file).read_text())
    except Exception as e:
        print(f"✗ cache unreadable: {e}")
        return 1

    task = _pf.resolve_from_cache(cache, query)
    if not task:
        print(f"✗ no task matched: {query}")
        return 1

    old = [l for l in task.get("labels", []) if l in DOMAINS]
    new_labels = swap_domain(task.get("labels", []), domain)
    try:
        _df._api("POST", f"/tasks/{task['id']}", {"labels": new_labels})
    except Exception as e:
        print(f"✗ Todoist update failed: {e}")
        return 1

    patch_cache_labels(cache, task["id"], new_labels)
    try:
        Path(cache_file).write_text(json.dumps(cache))
    except Exception:
        pass  # Todoist already updated; cache catches up on next refresh

    clean = _ANNOT.sub("", task["content"]).strip()
    arrow = f"{old[0]}→{domain}" if old else f"→{domain}"
    print(f"✎ {clean} {arrow}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
