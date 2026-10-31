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
import os
from pathlib import Path

# Never hardcode the token: this repo is public (Dream v172, 2026-10-29).
_TODOIST_TOKEN_FILE = Path.home() / ".config/todoist/token"
TODOIST_TOKEN = (_TODOIST_TOKEN_FILE.read_text().strip() if _TODOIST_TOKEN_FILE.exists()
                 else os.environ.get("TODOIST_API_KEY", ""))
BASE = "https://api.todoist.com/api/v1"
MUTATION_CAP_DEFAULT = 10
# The neon emitter (neon-task-checksum) mints the 😈 block-ritual tasks every 2h,
# including ~11:00 UTC inside the Dream window. Their "added" events are not
# Dream's (false-positive banner on v163 + v165, pattern 1161). Activity-log
# "added" extra_data carries only client/content/note_count — no labels — so
# match on the emitter's client + 😈 prefix. Dream's MCP writes show
# client "todoist-mcp/*" and are never exempt.
import re  # noqa: E402

# v170 (5) and v171 (16) still raised the ⛔ banner: every one of those "added"
# events came from client Python-urllib — the -1neon emitter (build-order-daemon),
# JM's /defer (defer-fast.py: a "deferred: …" posthoc record plus the one-off
# copy, created within a second of each other) and 0g/-1g block goals ("… {N}").
# Dream writes through todoist-mcp. Attribution is therefore by client first,
# content pattern second (for events that carry no client string).
DREAM_CLIENT_PREFIX = "todoist-mcp"
EMITTER_LABELS = {"0g", "-1g", "-1neon"}
_EMITTER_CONTENT = (
    re.compile(r"^😈"),                 # -1neon block rituals (build-order-daemon)
    re.compile(r"^deferred:"),          # /defer posthoc eval record (defer-fast.py)
    re.compile(r"\{\d+\}\s*$"),         # 0g / -1g block goals: "250分 「10」 {10}", "1n {7}"
)
DEFER_PAIR_SECONDS = 15


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def matches_emitter_content(extra: dict) -> bool:
    content = extra.get("content") or ""
    if any(p.search(content) for p in _EMITTER_CONTENT):
        return True
    return bool(EMITTER_LABELS & set(extra.get("labels") or []))


def classify_added(events: list[dict]) -> dict[str, list[dict]]:
    """Split "added" events into dream / emitter / other.

    dream   — created via todoist-mcp (Dream's only Todoist path), or carrying no
              client string and no known emitter pattern (fail loud, not quiet).
    emitter — a known non-Dream script: 😈 / deferred: / {N} content, a #0g/-1neon
              label, or the /defer copy created alongside a "deferred:" record.
    other   — any other non-MCP client (JM's apps, curl, ad-hoc scripts): listed in
              revert-mapping.json for inspection, never bannered.
    """
    defer_times = [_ts(e.get("event_date", "")) for e in events
                   if (e.get("extra_data", {}).get("content") or "").startswith("deferred:")]
    out: dict[str, list[dict]] = {"dream": [], "emitter": [], "other": []}
    for e in events:
        x = e.get("extra_data", {}) or {}
        client = x.get("client") or ""
        rec = {"id": str(e.get("object_id", "")),
               "content": (x.get("content", "") or "")[:100],
               "client": client,
               "event_date": e.get("event_date", "")}
        if client.startswith(DREAM_CLIENT_PREFIX):
            bucket = "dream"
        elif matches_emitter_content(x):
            bucket = "emitter"
        elif client and any(abs(_ts(rec["event_date"]) - t) <= DEFER_PAIR_SECONDS
                            for t in defer_times):
            bucket = "emitter"  # /defer's one-off copy of the deferred occurrence
        elif client:
            bucket = "other"
        else:
            bucket = "dream"
        out[bucket].append(rec)
    return out


def claude_start_from_log(run_dir: Path) -> str | None:
    """First "Starting claude" line of RUN/logs/agent-run.log (local time) → UTC ISO.

    The Dream window opens when claude starts, not when the launcher starts:
    intake runs for minutes first and creates nothing.
    """
    log = run_dir / "logs" / "agent-run.log"
    if not log.exists():
        return None
    m = re.search(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] Starting claude",
                  log.read_text(errors="replace"), re.M)
    if not m:
        return None
    local = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").astimezone()
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


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
    ap.add_argument("--since", default=None,
                    help="claude start, ISO8601 UTC (e.g. 2026-06-12T10:00:00); default: the "
                         "first 'Starting claude' line of RUN/logs/agent-run.log")
    ap.add_argument("--until", default=None, help="run end, ISO8601 UTC (default: now)")
    ap.add_argument("--cap", type=int, default=MUTATION_CAP_DEFAULT)
    args = ap.parse_args()

    run_dir = Path(args.run_dir).expanduser()
    if not args.since:
        args.since = claude_start_from_log(run_dir)
        if not args.since:
            print("ERROR: no --since and no 'Starting claude' line in logs/agent-run.log",
                  file=sys.stderr)
            return 1
    until = args.until or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    counts: dict[str, int] = {}
    restorable, no_prior, created, emitter, other = [], [], [], [], []
    # "added" is monitored because creation is PROHIBITED entirely (not budgeted):
    # any task Dream created is a policy violation to flag, not a mutation to count.
    for etype in ("added", "updated", "deleted", "completed"):
        try:
            evs = [e for e in fetch_events(etype, args.since) if e.get("event_date", "") <= until]
        except Exception as exc:  # network/API failure must not kill the dream pipeline
            print(f"WARN: activity fetch ({etype}) failed: {exc}", file=sys.stderr)
            counts[etype] = -1
            continue
        counts[etype] = len(evs)
        if etype == "added":
            split = classify_added(evs)
            created, emitter, other = split["dream"], split["emitter"], split["other"]
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
         "created": created, "emitter_added": emitter, "other_added": other, "restorable": restorable, "no_prior": no_prior},
        indent=1, ensure_ascii=False))

    mutations = max(counts.get("updated", 0), 0) + max(counts.get("deleted", 0), 0)
    print(f"audit: {counts} | created: {len(created)} (+{len(emitter)} emitter, "
          f"+{len(other)} other non-MCP clients, ignored) "
          f"| due-date changes: {len(restorable) + len(no_prior)} "
          f"| mutations vs cap: {mutations}/{args.cap}")

    alerts = []
    # Creation is an absolute violation — ANY created task gets flagged + listed to reap.
    if created:
        rows = "\n".join(f"- `{c['id']}` — {c['content']}" for c in created[:50])
        alerts.append(
            f"# ⛔ DREAM CREATED {len(created)} TODOIST TASK(S) — POLICY VIOLATION\n\n"
            f"Dream must NEVER create Todoist tasks (dream-prompt-base.md §No autonomous "
            f"Todoist task creation; JM 2026-06-22: the morning brief is the ONLY channel "
            f"to ask JM to do something). These were created this run and must be reaped:\n\n"
            f"{rows}\n\n"
            f"Reap each id (todoist delete-object) or re-propose as `[APPROVAL]` cards.\n\n---\n\n"
        )
    if mutations > args.cap:
        alerts.append(
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

    if not alerts:
        return 0

    brief = run_dir / "morning-brief.md"
    brief.write_text("".join(alerts) + (brief.read_text() if brief.exists() else ""))
    print(f"ALERT prepended to {brief}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
