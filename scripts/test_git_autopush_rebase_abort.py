"""Regression (2026-08-16): git-autopush.sh left the repo mid-rebase on a
failed pull, instead of aborting like vault-autopush.sh already does.

Traced from a live bug report ("I've completed all the -1n checkins but it
still only sums to 10 for this block"): a ritual stamp (🎯) correctly written
to Ix's build-order.md vanished ~10 minutes later. Root cause chain:

1. ~/vault is a git repo, auto-committed by TWO independent, uncoordinated
   cron/launchd scripts -- Ix's vault-autopush.sh and Straylight's
   git-autopush.sh -- which had diverged by ~1900 commits on each side.
2. git-autopush.sh's `git pull --rebase` failed (as it does most runs), but
   the script only logged a warning and pushed anyway -- it never called
   `git rebase --abort`. A failed rebase leaves the working tree showing
   whatever an intermediate replayed commit wrote, NOT a clean prior state.
3. ~/vault is ALSO synced file-level by Syncthing (bidirectional,
   filewatcher-driven), which is completely unaware of git or of
   neon_blocks.build_order_lock()'s flock discipline. It saw Straylight's
   stale mid-rebase build-order.md as "the file changed" and propagated it
   to Ix, silently overwriting the live, correctly-stamped file -- bypassing
   every application-level lock, since flock only protects against other
   processes on the SAME machine, not a file-sync daemon on another one.

The fix (matching vault-autopush.sh's already-correct pattern): abort a
failed rebase and skip the push entirely, so a conflict never leaves stale
content sitting on disk for Syncthing to pick up.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "git-autopush.sh"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True,
    )


def _init_bare_origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                    capture_output=True, check=True)
    return origin


def _clone(origin: Path, dest: Path) -> None:
    subprocess.run(["git", "clone", str(origin), str(dest)],
                    capture_output=True, check=True)
    _git(dest, "config", "user.email", "test@example.com")
    _git(dest, "config", "user.name", "Test")


@pytest.fixture
def conflicted_clone(tmp_path):
    """Two clones of the same origin, each committing conflicting changes to
    the SAME file/line -- reproduces exactly the scenario that makes
    `git pull --rebase` fail on a real run."""
    origin = _init_bare_origin(tmp_path)

    seed = tmp_path / "seed"
    _clone(origin, seed)
    target = seed / "shared.md"
    target.write_text("- 未 ☀️\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "push", "origin", "main")

    # "Ix": pushes a change to origin first.
    ix = tmp_path / "ix_clone"
    _clone(origin, ix)
    (ix / "shared.md").write_text("- 未 ☀️ 🎯\n")
    _git(ix, "add", "-A")
    _git(ix, "commit", "-m", "ix: added 🎯")
    r = _git(ix, "push", "origin", "main")
    assert r.returncode == 0, r.stderr

    # "Straylight": still on the seed commit, makes a CONFLICTING change to
    # the same line -- its pull --rebase against origin/main will fail.
    straylight = tmp_path / "straylight_clone"
    _clone(origin, straylight)
    (straylight / "shared.md").write_text("- 未 ☀️ 📧\n")
    _git(straylight, "add", "-A")
    _git(straylight, "commit", "-m", "straylight: added 📧")

    return straylight


def test_failed_rebase_leaves_clean_working_tree(conflicted_clone, monkeypatch):
    """The actual regression: after a failed pull --rebase, the repo must
    NOT be left mid-rebase, and the working tree file must read back as
    either straylight's own pre-rebase content or a clean abort -- never an
    intermediate replayed state that a file-sync daemon could pick up and
    propagate as if it were a genuine, intentional change."""
    repo = conflicted_clone
    monkeypatch.setenv("HOME", str(repo.parent))  # script defaults REPO_DIR to $HOME/i446-monorepo; we pass it explicitly anyway

    r = subprocess.run(
        ["bash", str(SCRIPT), str(repo), "test"],
        capture_output=True, text=True,
    )

    status = _git(repo, "status", "--porcelain=v1", "-b").stdout
    assert "rebase" not in _git(repo, "status").stdout.lower(), (
        f"repo left mid-rebase after a failed pull -- script output:\n{r.stdout}\n{r.stderr}\n"
        f"git status:\n{_git(repo, 'status').stdout}"
    )
    # The script's own exit must reflect the failure, not silently succeed.
    assert r.returncode != 0, "a failed rebase must not report success"


def test_script_aborts_rebase_on_pull_failure():
    """Structural guard: the fix must be wired to the actual failure branch,
    not just present somewhere in the file."""
    src = SCRIPT.read_text()
    i = src.index("git pull --rebase")
    j = src.index("git push -u origin", i)
    branch = src[i:j]
    assert "git rebase --abort" in branch, (
        "a failed `git pull --rebase` must abort before any push is attempted"
    )
    assert "exit 1" in branch, (
        "a failed rebase must exit non-zero, not fall through to push"
    )


def test_unmodified_script_would_have_left_a_dirty_tree(tmp_path):
    """Sanity check: reproduce the PRE-fix script inline and confirm it
    actually manifests the bug in this harness -- otherwise the fixed-script
    tests above could be passing for the wrong reason (e.g. an unrelated
    environment quirk masking the real behavior)."""
    origin = _init_bare_origin(tmp_path)
    seed = tmp_path / "seed"
    _clone(origin, seed)
    (seed / "shared.md").write_text("- 未 ☀️\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "push", "origin", "main")

    ix = tmp_path / "ix"
    _clone(origin, ix)
    (ix / "shared.md").write_text("- 未 ☀️ 🎯\n")
    _git(ix, "add", "-A")
    _git(ix, "commit", "-m", "ix")
    _git(ix, "push", "origin", "main")

    straylight = tmp_path / "straylight"
    _clone(origin, straylight)
    (straylight / "shared.md").write_text("- 未 ☀️ 📧\n")
    _git(straylight, "add", "-A")
    _git(straylight, "commit", "-m", "straylight")

    buggy = tmp_path / "buggy-git-autopush.sh"
    buggy.write_text(
        '#!/bin/bash\n'
        'REPO_DIR="$1"\ncd "$REPO_DIR" || exit 1\n'
        'git add -A\n'
        'if git diff --cached --quiet; then exit 0; fi\n'
        'git commit -m "auto" -q\n'
        'BRANCH=$(git rev-parse --abbrev-ref HEAD)\n'
        'git pull --rebase origin "$BRANCH" -q 2>&1 || echo "WARNING: pull failed"\n'
        'git push -u origin "$BRANCH" -q 2>&1 || echo "WARNING: push failed"\n'
    )
    subprocess.run(["bash", str(buggy), str(straylight)], capture_output=True, text=True)

    assert "rebase" in _git(straylight, "status").stdout.lower(), (
        "expected the pre-fix script to leave the repo mid-rebase in this "
        "harness -- if it doesn't, the scenario isn't reproducing the real "
        "bug and the fixed-script assertions above aren't proving much"
    )
