"""Regression (2026-10-24): Ix's monorepo checkout sat on ios-nits-2026-10-22
for two days and git-autopush.sh, which pushes "the current branch", sent 173
snapshots there instead of wip. On the wrong branch both autopush scripts must
commit nothing, push nothing, and raise a critical alert."""
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).parent


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def _repo(tmp, branch):
    origin = tmp / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "wip")
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "init")
    _git(repo, "remote", "add", "origin", str(origin)); _git(repo, "push", "-q", "origin", "wip")
    if branch != "wip":
        _git(repo, "checkout", "-q", "-b", branch)
    (repo / "a.txt").write_text("2\n")                 # a pending edit autopush would commit
    return repo, origin


def _run(script, repo, home, **env):
    e = {**os.environ, "HOME": str(home), **env}
    return subprocess.run(["bash", str(HERE / script), str(repo)], env=e, capture_output=True, text=True)


def _commits(repo):
    return int(subprocess.run(["git", "rev-list", "--all", "--count"], cwd=repo,
                              capture_output=True, text=True).stdout)


def test_wrong_branch_commits_nothing_and_alerts(tmp_path):
    repo, origin = _repo(tmp_path, "ios-nits-2026-10-22")
    home = tmp_path / "home"; home.mkdir()
    r = _run("git-autopush.sh", repo, home, AUTOPUSH_EXPECT_BRANCH="wip")
    assert r.returncode == 2 and "expected 'wip'" in r.stdout
    assert _commits(repo) == 1                          # nothing committed
    assert "ios-nits" not in subprocess.run(["git", "branch"], cwd=origin, capture_output=True, text=True).stdout
    alert = json.loads((home / "vault/z_ibx/alerts.jsonl").read_text().splitlines()[-1])
    assert alert["severity"] == "critical" and alert["reason"] == "wrong_branch"


def test_expected_branch_still_commits(tmp_path):
    repo, _ = _repo(tmp_path, "wip")
    home = tmp_path / "home"; home.mkdir()
    r = _run("git-autopush.sh", repo, home, AUTOPUSH_EXPECT_BRANCH="wip")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _commits(repo) == 2


def test_vault_autopush_has_the_same_guard():
    src = (HERE / "vault-autopush.sh").read_text()
    guard = src.index("Branch guard"); stage = src.index("git add -A")
    assert guard < stage and "autopush_branch_alert.py" in src


def test_card_text_names_repo_branch_and_fix():
    import importlib.util
    spec = importlib.util.spec_from_file_location("aba", HERE / "autopush_branch_alert.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    content, desc = m.card_text("/Users/x/i446-monorepo", "ios-nits", "wip")
    assert "i446-monorepo is on ios-nits, not wip" in content and "symbolic-ref HEAD refs/heads/wip" in desc
