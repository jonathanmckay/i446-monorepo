#!/usr/bin/env python3
"""push_auto: record the daily `push` habit when GitHub already has today's push.

JM 2026-11-22: "can we make push something that automatically-records based on
if something gets pushed to github before noon on that day. If it doesn't then
it appears as a task on dtd that day."

The recurring `push` card stays (config/daily-todoist-manifest.json recreates
it at 04:15 anyway, and did-fast is the one path that marks 0n, credits the
points, closes the card and stamps all-colours). This job runs on Ix through
the morning (com.jm.push-auto, 04:35 to 11:55) and, the first time GitHub holds
a commit of JM's dated today on a branch autopush pushes to, completes the card
the same way dtd would: `did-fast.py push`. Nothing pushed by noon means the
card is simply still in dtd, and completing it there works as before.

Snapshots count (COUNT_SNAPSHOTS): autopush's own `auto:` / `vault backup:`
commits are pushes, so on a healthy day this credits itself and the card shows
only when the pipeline is broken. Set it False to require a commit written by
hand.

Never marks twice: it acts only while the card is due today, and did-fast
refuses a 0n cell that is already marked. Every failure (gh, Todoist, Excel
down) leaves the day unmarked and the next run tries again.

Usage: push_auto.py [--check]     --check prints the verdict and writes nothing
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HOME = Path.home()
DID_FAST = HOME / "i446-monorepo" / "tools" / "did" / "did-fast.py"
VAULT_HEALTH = HOME / "i446-monorepo" / "tools" / "vault" / "vault_health.py"
TOKEN = HOME / ".config" / "todoist" / "token"
MARK_DIR = HOME / ".cache" / "jm"
HABIT = "push"
NOON = 12
COUNT_SNAPSHOTS = True
SNAPSHOT_PREFIXES = ("auto:", "vault backup:")


def _vault_health():
    spec = importlib.util.spec_from_file_location("vault_health_push_auto", VAULT_HEALTH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def query(repos: list, since: dt.datetime) -> str:
    """One GraphQL query: who is asking, and each pushed branch's commits since
    `since` as GitHub holds them."""
    iso = since.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = []
    for i, (full, branch) in enumerate(repos):
        owner, name = full.split("/")
        parts.append(
            f'r{i}: repository(owner:"{owner}",name:"{name}"){{ref(qualifiedName:"refs/heads/{branch}")'
            f'{{target{{... on Commit{{history(since:"{iso}",first:50)'
            f'{{nodes{{committedDate messageHeadline author{{user{{login}}}}}}}}}}}}}}}}')
    return "{viewer{login} %s}" % " ".join(parts)


def pushed_today(data: dict, day_start: dt.datetime, cutoff: dt.datetime,
                 count_snapshots: bool = COUNT_SNAPSHOTS) -> list:
    """The commits that count: on GitHub, attributed to the asking account,
    dated from `day_start` up to `cutoff` (noon). [(repo key, iso, headline)]."""
    login = ((data.get("viewer") or {}).get("login") or "").lower()
    if not login:
        return []
    out = []
    for key, repo in data.items():
        if key == "viewer" or not repo:
            continue
        nodes = ((((repo.get("ref") or {}).get("target") or {}).get("history") or {}).get("nodes")) or []
        for n in nodes:
            who = (((n.get("author") or {}).get("user") or {}).get("login") or "").lower()
            if who != login:
                continue
            try:
                when = dt.datetime.fromisoformat((n.get("committedDate") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if not (day_start <= when < cutoff):
                continue
            head = n.get("messageHeadline") or ""
            if not count_snapshots and head.startswith(SNAPSHOT_PREFIXES):
                continue
            out.append((key, when.isoformat(), head))
    return out


def card_due_today(tasks: list, today: dt.date) -> dict | None:
    """The open `push` habit card if it is due today or overdue, else None
    (already completed today: its next occurrence is tomorrow)."""
    for t in tasks:
        name = (t.get("content") or "").strip().lower()
        if not (name == HABIT or name.startswith(HABIT + " ")):
            continue
        if "0neon" not in (t.get("labels") or []):
            continue
        due = ((t.get("due") or {}).get("date") or "")[:10]
        if due and due <= today.isoformat():
            return t
    return None


def _todoist_tasks() -> list:
    req = urllib.request.Request("https://api.todoist.com/api/v1/tasks?label=" + HABIT,
                                 headers={"Authorization": "Bearer " + TOKEN.read_text().strip()})
    j = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return j.get("results", j) if isinstance(j, dict) else j


def main(argv: list, now: dt.datetime | None = None) -> int:
    check = "--check" in argv
    now = now or dt.datetime.now().astimezone()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    if now.hour >= NOON and not check:
        print(f"{stamp} past noon: the card stays in dtd")
        return 0
    mark = MARK_DIR / f"push-auto-{now.date().isoformat()}"
    if mark.exists() and not check:
        return 0
    try:
        card = card_due_today(_todoist_tasks(), now.date())
    except Exception as e:  # noqa: BLE001
        print(f"{stamp} todoist unreachable, will retry: {e}")
        return 1
    if card is None:
        print(f"{stamp} push card is not due today (already done): nothing to do")
        if not check:
            MARK_DIR.mkdir(parents=True, exist_ok=True)
            mark.touch()
        return 0
    vh = _vault_health()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    data = vh._gh_graphql(query(vh.GH_REPOS, day_start))
    if data.get("_error"):
        print(f"{stamp} github unreachable, will retry: {data['_error']}")
        return 1
    hits = pushed_today(data, day_start, min(now, day_start.replace(hour=NOON)))
    if not hits:
        print(f"{stamp} nothing of mine on GitHub yet today: card stays")
        return 0
    print(f"{stamp} {len(hits)} commit(s) on GitHub today, latest: {hits[0][2][:60]!r}")
    if check:
        return 0
    r = subprocess.run([sys.executable, str(DID_FAST), HABIT], capture_output=True, text=True, timeout=180)
    out = (r.stdout or "").strip()
    print(f"{stamp} did-fast rc={r.returncode}: {out[-300:]}")
    try:
        still_due = card_due_today(_todoist_tasks(), now.date()) is not None
    except Exception:  # noqa: BLE001
        still_due = True
    if r.returncode != 0 or still_due:
        print(f"{stamp} push not recorded (card still due): will retry")
        return 1
    MARK_DIR.mkdir(parents=True, exist_ok=True)
    mark.touch()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
