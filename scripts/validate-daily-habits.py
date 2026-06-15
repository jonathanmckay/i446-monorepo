#!/usr/bin/env python3
"""validate-daily-habits.py — ensure every canonical daily habit has an open
recurring Todoist task for the new day, recreating any that are missing.

Recurring Todoist tasks occasionally fail to regenerate for a new day (a sync
hiccup, an accidental delete, a completion that didn't roll forward). When that
happens the habit silently vanishes from the day's queue and the only way to
notice is cross-checking the Neon sheet. This validates the canonical set
(config/daily-todoist-manifest.json) against live Todoist and, with --fix,
recreates the missing ones from their stored spec.

Usage:
    validate-daily-habits.py            # report missing as JSON
    validate-daily-habits.py --fix      # recreate missing, then report
    validate-daily-habits.py --pretty   # human-readable summary

Output (JSON): {"checked": N, "present": [...], "missing": [...],
                "recreated": [...], "errors": [...]}
The morning wakeup flow reads this to show a "missing habits" card.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "config" / "daily-todoist-manifest.json"
DEFAULT_CACHE = Path.home() / ".cache/jm/daily-habits-check.json"
API = "https://api.todoist.com/api/v1"
AUTO_MARK = "😈"  # prefixes every task created by automation (see stale-contacts.py)


def bare(content: str) -> str:
    """Normalize a task content to its bare habit name: strip the 😈 auto-marker
    and (N)/[N]/{N} estimate tokens, collapse whitespace, lowercased. Mirrors the
    manifest builder so manifest `match` strings line up with live task contents
    — including habits we auto-recreated with the 😈 prefix (so we don't re-flag
    and duplicate them)."""
    s = content.lstrip(AUTO_MARK).strip()
    s = re.sub(r"\s*[\[\(\{][^\]\)\}]*[\]\)\}]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def compute_missing(manifest: dict, present_contents: list[str]) -> list[str]:
    """Pure: return manifest habit keys that have no matching open task.

    A habit matches if its bare `match` name equals a present task's bare name,
    or (for multi-word names) is contained in one — covers '早餐' vs '早餐 (15) [5]'
    and 'ibx s897' vs 'ibx s897 [6] (15)' without false-matching short tokens."""
    present = {bare(c) for c in present_contents}
    missing = []
    for key, h in manifest["habits"].items():
        name = bare(h["match"])
        hit = name in present or any(
            name == p or (len(name) > 3 and (name in p or p in name)) for p in present
        )
        if not hit:
            missing.append(key)
    return missing


def recreate_payload(habit: dict) -> dict:
    """Pure: build the Todoist create-task body from a manifest entry. The 😈
    auto-marker is prefixed so a habit Dream/this job rebuilt is visibly distinct
    from one Todoist regenerated on its own (bare() strips it back off for
    matching, and /did's query-in-task overlap is unaffected)."""
    body = {
        "content": f"{AUTO_MARK} {habit['content']}",
        "due_string": habit.get("due_string", "every day"),
        "labels": habit.get("labels", []),
        "priority": habit.get("priority", 1),
    }
    if habit.get("project_id"):
        body["project_id"] = habit["project_id"]
    return body


# ------- I/O (not exercised by unit tests) -------

def _token() -> str:
    import os
    tok = os.environ.get("TODOIST_API_KEY")
    if tok:
        return tok
    cfg = json.loads((Path.home() / ".claude.json").read_text())
    auth = cfg["mcpServers"]["todoist"]["headers"]["Authorization"]
    return auth.split(None, 1)[1].strip()


def _fetch_open(token: str) -> list[str]:
    contents = []
    for label in ("0neon", "夜neon"):
        q = urllib.parse.quote(f"@{label}")
        url = f"{API}/tasks/filter?query={q}&limit=200"
        while url:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            d = json.load(urllib.request.urlopen(req))
            contents += [t["content"] for t in d.get("results", [])]
            c = d.get("next_cursor")
            url = f"{API}/tasks/filter?query={q}&limit=200&cursor={urllib.parse.quote(c)}" if c else None
    return contents


def _create(token: str, body: dict) -> None:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API}/tasks", data=data, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    urllib.request.urlopen(req)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="recreate missing tasks")
    ap.add_argument("--pretty", action="store_true", help="human-readable output")
    ap.add_argument("--cache", nargs="?", const=str(DEFAULT_CACHE), default=None,
                    help="also write the result JSON to this path (default: %(default)s)")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    token = _token()
    present_contents = _fetch_open(token)
    missing = compute_missing(manifest, present_contents)

    recreated, errors = [], []
    if args.fix:
        for key in missing:
            try:
                _create(token, recreate_payload(manifest["habits"][key]))
                recreated.append(key)
            except Exception as e:  # noqa: BLE001
                errors.append({"habit": key, "error": str(e)[:120]})

    from datetime import date as _date
    result = {
        "date": _date.today().isoformat(),
        "checked": len(manifest["habits"]),
        "present": len(manifest["habits"]) - len(missing),
        "missing": missing,
        "missing_names": [manifest["habits"][k]["match"] for k in missing],
        "recreated": recreated,
        "recreated_names": [manifest["habits"][k]["match"] for k in recreated],
        "errors": errors,
    }
    if args.cache:
        cpath = Path(args.cache)
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps(result, ensure_ascii=False))
    if args.pretty:
        if not missing:
            print(f"✅ all {result['checked']} daily habits present in Todoist")
        else:
            verb = "recreated" if args.fix else "MISSING"
            print(f"⚠ {len(missing)} daily habits {verb}: " +
                  ", ".join(manifest["habits"][k]["match"] for k in missing))
            for e in errors:
                print(f"   ✗ {e['habit']}: {e['error']}")
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
