"""Regression (2026-11-08): git-autopush.sh logged
"ERROR: commit failed (pre-commit hook rejected it?)" ~80 times a day from
2026-10-17 19:50 on (1316 lines; none before), and the weekly /1-i446
git-autopush check failed on it.

Neither ~/i446-monorepo nor ~/vault has a commit hook. The cause was a second
committer: the ~/.claude/skills backup cron (`*/10 ... git add -A && git
commit`) became a commit of the whole monorepo when ~/.claude/skills was
symlinked into skills/claude-skills on 10-17 19:41, and fired on the same
tick. When it held .git/index.lock mid-commit, git-autopush's `git commit`
died with "Unable to create .../index.lock: File exists", and the raced check
(`git diff --cached --quiet`) still saw staged changes because the other
commit had not moved HEAD yet -> false ERROR, exit 1, no push. The cron line
is gone (93a65954); this guards the script against any concurrent committer
(manual commits from sessions land in the same repo) and checks that a real
failure logs git's own reason instead of a guess.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).parent / "git-autopush.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True,
                   capture_output=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.md").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    _git(repo, "push", "-q", "origin", "main")
    return repo


def _run(repo: Path, **env) -> subprocess.Popen:
    return subprocess.Popen(["bash", str(SCRIPT), str(repo), "auto"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env={**os.environ, **env})


def test_concurrent_commit_holding_index_lock_is_a_race_not_an_error(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.md").write_text("two\n")
    # The other committer has staged the change, is mid-commit holding the
    # index lock, and has not moved HEAD yet.
    _git(repo, "add", "-A")
    tree = _git(repo, "write-tree")
    lock = repo / ".git" / "index.lock"
    lock.write_text("")

    p = _run(repo)
    time.sleep(1.5)
    commit = _git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "racer")
    _git(repo, "update-ref", "HEAD", commit)
    lock.unlink()
    out, _ = p.communicate(timeout=60)

    assert "ERROR" not in out, out
    assert p.returncode == 0, out
    # It waits out the lock and then finds the change already committed
    # (either before staging or after its own commit attempt).
    assert "commit raced" in out or "unpushed commit" in out, out
    assert _git(repo, "rev-parse", "origin/main") == commit, "raced commit must still be pushed"


def test_lock_released_without_commit_retries_and_commits(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.md").write_text("three\n")
    lock = repo / ".git" / "index.lock"
    lock.write_text("")  # some short git op (status refresh) holds the lock

    p = _run(repo)
    time.sleep(1.5)
    lock.unlink()
    out, _ = p.communicate(timeout=60)

    assert p.returncode == 0, out
    assert "committed:" in out, out
    assert _git(repo, "show", "origin/main:a.md") == "three"


def test_real_commit_failure_logs_gits_reason(tmp_path):
    repo = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'blocked by test hook' >&2\nexit 1\n")
    hook.chmod(0o755)
    (repo / "a.md").write_text("four\n")

    p = _run(repo)
    out, _ = p.communicate(timeout=60)

    assert p.returncode == 1, out
    err = [l for l in out.splitlines() if "ERROR: commit failed" in l]
    assert err and "blocked by test hook" in err[0], out
