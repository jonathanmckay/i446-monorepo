#!/usr/bin/env python3
"""dream-audit-todoist.py — post-run tripwire for Dream's Todoist mutations.

Born from incident v33 (2026-06-12): a dream run executed 85 task reschedules
under the "reversible = execute" policy, emptied JM's today queue, and its
changelog lacked the revert data. The prompt now carries a hard cap of
MUTATION_CAP modifications to pre-existing tasks per run — this script is the
enforcement layer that does not trust the model to obey the prompt.

What it does, independently of anything the run wrote about itself:
1. Pulls the Todoist activity log (updated/deleted/completed item events)
   for the run window.
2. Writes RUN_DIR/revert-mapping.json with every due-date change and its
   prior value (the activity log records last_due_date — authoritative).
3. If mutations exceed the cap, prepends an ALERT block to morning-brief.md
   so the violation is the first thing JM sees.

Usage:
    dream-audit-todoist.py --run-dir <dir> --since <ISO8601-UTC> [--cap 10]

Exit codes: 0 = within budget, 2 = budget exceeded (alert written), 1 = error.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TODOIST_TOKEN = "7eb82f47aba8b334769351368e4e3e3284f980e5"
BASE = "https://api.todoist.com/api/v1"
MUTATION_CAP_DEFAULT = 10


def _api(url: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TODOIST_TOKEN}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def fetch_events(event_type: str, since_iso: str) -> list[dict]:
    """Page the v1 activity log back to `since_iso` for one event type."""
    events, cursor = [], None
    for _ in range(10):
        url = f"{BASE}/activities?object_type=item&event_type={event_type}&limit=100"
        if cursor:
            url += f"&cursor={cursor}"
        raw = _api(url)
        batch = raw.get("results", raw if isinstance(raw, list) else [])
        events.extend(batch)
        if batch and min(e.get("event_date", "") for e in batch) < since_iso:
            break
        cursor = raw.get("next_cursor") if isinstance(raw, dict) else None
        if not cursor:
            break
    return [e for e in events if e.get("event_date", "") >= since_iso]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--since", required=True, help="run start, ISO8601 UTC (e.g. 2026-06-12T10:00:00)")
    ap.add_argument("--until", default=None, help="run end, ISO8601 UTC (default: now)")
    ap.add_argument("--cap", type=int, default=MUTATION_CAP_DEFAULT)
    args = ap.parse_args()

    run_dir = Path(args.run_dir).expanduser()
    until = args.until or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    counts: dict[str, int] = {}
    restorable, no_prior = [], []
    for etype in ("updated", "deleted", "completed"):
        try:
            evs = [e for e in fetch_events(etype, args.since) if e.get("event_date", "") <= until]
        except Exception as exc:  # network/API failure must not kill the dream pipeline
            print(f"WARN: activity fetch ({etype}) failed: {exc}", file=sys.stderr)
            counts[etype] = -1
            continue
        counts[etype] = len(evs)
        if etype == "updated":
            for e in evs:
                x = e.get("extra_data", {})
                if not x.get("due_date"):
                    continue  # priority/description-only edits
                rec = {"id": str(e["object_id"]), "content": x.get("content", "")[:80],
                       "old": x.get("last_due_date"), "new": x.get("due_date"),
                       "event_date": e.get("event_date", "")}
                (restorable if rec["old"] else no_prior).append(rec)

    mapping_path = run_dir / "revert-mapping.json"
    mapping_path.write_text(json.dumps(
        {"window": {"since": args.since, "until": until}, "counts": counts,
         "restorable": restorable, "no_prior": no_prior},
        indent=1, ensure_ascii=False))

    mutations = max(counts.get("updated", 0), 0) + max(counts.get("deleted", 0), 0)
    print(f"audit: {counts} | due-date changes: {len(restorable) + len(no_prior)} "
          f"| mutations vs cap: {mutations}/{args.cap}")

    if mutations <= args.cap:
        return 0

    alert = (
        f"# ⚠ DREAM MUTATION BUDGET EXCEEDED\n\n"
        f"This run made **{mutations} Todoist mutations** "
        f"(updated: {counts.get('updated')}, deleted: {counts.get('deleted')}, "
        f"completed: {counts.get('completed')}) against a cap of {args.cap}.\n"
        f"Per policy (dream-prompt-base.md → Todoist mutation budget) bulk changes "
        f"must be staged for approval, not executed.\n\n"
        f"**Auto-generated revert mapping:** `{mapping_path}` "
        f"({len(restorable)} restorable due-date changes, {len(no_prior)} without prior date).\n"
        f"To revert: feed `restorable` to reschedule-tasks / the revert recipe in "
        f"session 0a4c0b46 (2026-06-12).\n\n---\n\n"
    )
    brief = run_dir / "morning-brief.md"
    brief.write_text(alert + (brief.read_text() if brief.exists() else ""))
    print(f"ALERT prepended to {brief}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
