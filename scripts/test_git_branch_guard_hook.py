"""2026-10-24: block branch switches in the shared ~/vault / i446-monorepo
checkouts (a stray `git checkout -b` there sent autopush to the wrong branch
for two days); worktrees and file restores stay allowed."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("gbg", HERE / "git-branch-guard-hook.py")
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
MONO = os.path.expanduser("~/i446-monorepo")
VAULT = os.path.expanduser("~/vault")


def test_blocks_branch_switches_in_shared_tree():
    for cmd in ["git checkout -b ios-nits-2026-10-22", "git switch wip", "git checkout main",
                f"cd {MONO} && git checkout -b x", f"git -C {VAULT} switch -c y"]:
        assert g.blocked(cmd, MONO), cmd


def test_allows_worktrees_file_restores_and_other_dirs():
    for cmd in ["git status", "git checkout -- tools/x.py", "git worktree add --detach /private/tmp/w origin/wip",
                "cd /private/tmp/w && git checkout -q --detach origin/wip", "git log --oneline",
                "cd $WT && git checkout -q --detach origin/wip"]:
        assert g.blocked(cmd, MONO) is None, cmd
    assert g.blocked("git checkout -b feature", "/private/tmp/somewhere") is None


def test_hook_exit_code_blocks_via_stdin_json():
    payload = json.dumps({"tool_name": "Bash", "cwd": MONO, "tool_input": {"command": "git checkout -b z"}})
    r = subprocess.run(["python3", str(HERE / "git-branch-guard-hook.py")], input=payload, text=True, capture_output=True)
    assert r.returncode == 2 and "worktree" in r.stderr


def test_relative_dash_c_resolves_against_prior_cd():
    """v169 review: `-C rel` is relative to the tracked cd, not the hook's cwd."""
    assert g.blocked("cd /private/tmp/wt && git -C sub checkout -b x", VAULT) is None
    assert g.blocked(f"cd {os.path.dirname(VAULT)} && git -C vault switch x", "/private/tmp")
