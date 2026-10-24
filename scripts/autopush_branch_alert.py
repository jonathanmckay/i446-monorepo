#!/usr/bin/env python3
"""autopush_branch_alert — raise the alarm when an autopush repo is off its branch.

Called by git-autopush.sh (monorepo → wip) and vault-autopush.sh (vault → main)
when HEAD is not the expected branch; they then skip the commit and push.
(2026-10-24: a session ran `git checkout -b ios-nits-2026-10-22` in Ix's shared
monorepo checkout; autopush followed HEAD and pushed 173 snapshots to that
branch for two days while only warning-level alerts piled up.)

Does two things, so it reaches JM rather than a headless Ix screen:
  1. one critical line in ~/vault/z_ibx/alerts.jsonl (every run);
  2. one dtd card per repo per day (Todoist, due today, @i447) with the fix.
Switching back is NOT automated: the wrong branch may hold half-done work.

Usage: autopush_branch_alert.py <repo_dir> <current_branch> <expected_branch>
"""
from __future__ import annotations

import datetime as dt
import json
import socket
import sys
import urllib.request
from pathlib import Path

ALERTS = Path.home() / "vault" / "z_ibx" / "alerts.jsonl"
TOKEN = Path.home() / ".config" / "todoist" / "token"
MARK_DIR = Path.home() / ".cache" / "jm"


def card_text(repo: str, current: str, expected: str) -> tuple[str, str]:
    name = Path(repo).name
    content = f"git autopush stopped: {name} is on {current}, not {expected} (15) [0]"
    desc = (f"Autopush on {socket.gethostname().split('.')[0]} skips commits while `{repo}` is on "
            f"`{current}`; nothing is lost (files still sync), but nothing reaches `{expected}`.\n\n"
            "Fix (working tree untouched):\n"
            f"1. `cd {repo} && git fetch origin`\n"
            f"2. keep anything only on `{current}`: merge it into `{expected}` in a worktree, push\n"
            f"3. `git branch -f {expected} origin/{expected} && git symbolic-ref HEAD refs/heads/{expected} "
            f"&& git reset --mixed origin/{expected}`\n\n"
            "Branch work belongs in `git worktree add`, never a checkout in the shared tree.")
    return content, desc


def main(argv: list[str]) -> int:
    repo, current, expected = argv[1], argv[2], argv[3]
    content, desc = card_text(repo, current, expected)
    ALERTS.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERTS, "a") as f:
        f.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "host": socket.gethostname().split(".")[0], "tool": "git-autopush",
                            "severity": "critical", "reason": "wrong_branch",
                            "detail": f"{repo} on {current}, expected {expected}; autopush skipped"},
                           ensure_ascii=False) + "\n")
    mark = MARK_DIR / f"autopush-branch-card-{Path(repo).name}-{dt.date.today().isoformat()}"
    if mark.exists():
        return 0
    try:
        body = json.dumps({"content": content, "description": desc, "labels": ["i447"],
                           "due_string": "today", "priority": 4}).encode()
        req = urllib.request.Request("https://api.todoist.com/api/v1/tasks", data=body, method="POST",
                                     headers={"Authorization": "Bearer " + TOKEN.read_text().strip(),
                                              "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
        MARK_DIR.mkdir(parents=True, exist_ok=True)
        mark.touch()
    except Exception as e:  # the alerts.jsonl line above still stands
        print(f"autopush_branch_alert: card not created: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
