#!/usr/bin/env python3
"""merge.py <task_id> — land an accepted agent branch on wip.

Spawned detached by picks.on_close() when JM accepts (⌥⏎) a task whose receipt
has a branch; never runs inside did-fast's 120 s watchdog. Works in its own
worktree (never a checkout in ~/i446-monorepo: autopush commits to whatever is
checked out). A merge conflict leaves wip untouched and raises an alert.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import picks  # noqa: E402


def git(*args: str, cwd: Path = picks.REPO) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def merge(task_id: str) -> bool:
    br = picks.branch(task_id)
    if not br:
        return False
    wt = Path(f"/private/tmp/agent-merge-{task_id}")
    if wt.exists():
        git("worktree", "remove", "--force", str(wt))
    try:
        if git("fetch", "-q", "origin", "wip", br).returncode:
            raise RuntimeError(f"fetch {br} failed")
        if git("worktree", "add", "-q", "--detach", str(wt), "origin/wip").returncode:
            raise RuntimeError("worktree add failed")
        r = git("merge", "--no-edit", "-m", f"Merge {br} (accepted agent pick {task_id})",
                f"origin/{br}", cwd=wt)
        if r.returncode:
            git("merge", "--abort", cwd=wt)
            raise RuntimeError("merge conflict: " + (r.stdout + r.stderr)[-300:])
        if git("push", "-q", "origin", "HEAD:wip", cwd=wt).returncode:
            raise RuntimeError("push to wip failed")
        git("push", "-q", "origin", "--delete", br)
        picks.log("merged", task_id, branch=br)
        return True
    except RuntimeError as e:
        picks.log("merge_failed", task_id, branch=br, reason=str(e))
        picks.alert("merge", f"agent branch {br} not merged: {e}", severity="warning")
        return False
    finally:
        git("worktree", "remove", "--force", str(wt))


if __name__ == "__main__":
    sys.exit(0 if merge(sys.argv[1]) else 1)
