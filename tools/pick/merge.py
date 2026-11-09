#!/usr/bin/env python3
"""merge.py <task_id> — land an accepted agent branch.

Spawned detached by picks.on_close() when JM accepts (⌥⏎) a task whose receipt
has a branch; never runs inside did-fast's 120 s watchdog. One branch per repo
the worker touched (picks.branches). How it lands is the repo's `land` in
picks.REPOS: "merge" merges into origin/<base> in its own worktree and pushes
(monorepo wip; never a checkout in ~/i446-monorepo, autopush commits to
whatever is checked out); "pr" opens a GitHub PR onto <base> with the receipt
as the body (the dashboards team lands main by PR and CI deploys it; main is
not branch-protected, so this is convention, enforced only by the worker's
allowlist). An existing open PR for the branch is reused. A merge conflict
leaves base untouched and raises an alert; so does a branch file that names
no known repo, which would otherwise look landed.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import picks  # noqa: E402


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _slug(repo: Path) -> str | None:
    """owner/name from the origin URL (ssh or https)."""
    url = git("remote", "get-url", "origin", cwd=repo).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def _receipt_body(task_id: str) -> str:
    try:
        text = (picks.receipt_dir(task_id) / "receipt.md").read_text()
    except OSError:
        return ""
    return text[:6000]


def land_merge(task_id: str, repo: Path, base: str, br: str) -> None:
    wt = Path(f"/private/tmp/agent-merge-{task_id}-{repo.name}")
    if wt.exists():
        git("worktree", "remove", "--force", str(wt), cwd=repo)
    try:
        if git("fetch", "-q", "origin", base, br, cwd=repo).returncode:
            raise RuntimeError(f"fetch {br} failed")
        if git("worktree", "add", "-q", "--detach", str(wt), f"origin/{base}", cwd=repo).returncode:
            raise RuntimeError("worktree add failed")
        r = git("merge", "--no-edit", "-m", f"Merge {br} (accepted agent pick {task_id})",
                f"origin/{br}", cwd=wt)
        if r.returncode:
            git("merge", "--abort", cwd=wt)
            raise RuntimeError("merge conflict: " + (r.stdout + r.stderr)[-300:])
        if git("push", "-q", "origin", f"HEAD:{base}", cwd=wt).returncode:
            raise RuntimeError(f"push to {base} failed")
        git("push", "-q", "origin", "--delete", br, cwd=repo)
    finally:
        git("worktree", "remove", "--force", str(wt), cwd=repo)


def land_pr(task_id: str, repo: Path, base: str, br: str) -> str:
    slug = _slug(repo)
    if not slug:
        raise RuntimeError("origin is not a GitHub remote")
    # a second accept (or a retry) must not fail on "PR already exists"
    r = subprocess.run(["gh", "pr", "list", "--repo", slug, "--head", br, "--state", "open",
                        "--json", "url", "--jq", ".[0].url"], capture_output=True, text=True, cwd=repo)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    body = (f"Accepted agent pick {task_id} (tools/pick). Receipt:\n\n{_receipt_body(task_id)}\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)")
    r = subprocess.run(["gh", "pr", "create", "--repo", slug, "--head", br, "--base", base,
                        "--title", f"Agent pick {task_id}", "--body", body],
                       capture_output=True, text=True, cwd=repo)
    if r.returncode:
        raise RuntimeError("gh pr create failed: " + (r.stdout + r.stderr)[-300:])
    return r.stdout.strip()


def merge(task_id: str) -> bool:
    todo = picks.branches(task_id)
    if not todo:
        if picks.branch(task_id):
            picks.log("merge_failed", task_id, reason="branch file names no known repo")
            picks.alert("merge", f"agent pick {task_id}: branch file names no known repo "
                                 f"({picks.branch(task_id)!r}); nothing landed", severity="warning")
        return False
    ok = True
    for key, br in todo:
        cfg = picks.REPOS[key]
        repo, base = cfg["path"], cfg["base"]
        try:
            if cfg["land"] == "pr":
                url = land_pr(task_id, repo, base, br)
                picks.log("pr_opened", task_id, repo=key, branch=br, url=url)
            else:
                land_merge(task_id, repo, base, br)
                picks.log("merged", task_id, repo=key, branch=br)
        except RuntimeError as e:
            ok = False
            picks.log("merge_failed", task_id, repo=key, branch=br, reason=str(e))
            picks.alert("merge", f"agent branch {br} ({key}) not landed: {e}", severity="warning")
    return ok


if __name__ == "__main__":
    sys.exit(0 if merge(sys.argv[1]) else 1)
