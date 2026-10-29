"""Tests for scripts/git-hooks/secret_scan.py and install.sh.

Every fake secret is assembled at runtime, so this file never matches the
scanner itself (it is committed through the hook it tests).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import secret_scan as ss  # noqa: E402

A36 = "Ab1" * 12
FAKES = {
    "github-classic": "gh" + "p_" + A36,
    "github-fine-grained": "github" + "_pat_" + ("11AB" + "x" * 18) + "_" + "Y" * 59,
    "google-oauth-secret": "GOC" + "SPX-" + "a1B2c3D4e5F6g7H8i9J0kLmN",
    "google-api-key": "AI" + "za" + "B" * 35,
    "slack": "xo" + "xb-" + "1234567890-abcdefghij",
    "anthropic": "sk-" + "ant-" + "api03-" + "Q" * 30,
    "openai": "sk-" + "proj-" + "R7" * 20,
    "aws-access-key": "AK" + "IA" + "ABCDEFGHIJ234567",
    "azure-client-secret": "EF8" + "8Q~" + "a" * 34,
    "private-key": "-----BEGIN " + "RSA PRIVATE KEY-----",
}
HEX40 = "a1" + "c2" + "0123456789abcdef0123456789abcdef0123"
HEX32 = "b0" + "36" + "0123456789abcdef0123456789ab"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    # Never read the machine's real ~/.config/todoist/token in tests.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SECRET_SCAN_KNOWN_FILES", raising=False)
    monkeypatch.setenv("SECRET_SCAN_ALERTS", str(tmp_path / "alerts.jsonl"))


@pytest.mark.parametrize("rule,value", sorted(FAKES.items()))
def test_each_pattern_is_caught(rule, value):
    hits = ss.scan_line(f'X = "{value}"', "f.py", 1, [])
    assert [h.rule for h in hits][:1] == [rule], hits


def test_hex40_only_near_a_keyword():
    assert ss.scan_line(f"commit {HEX40}", "f", 1, []) == []
    hits = ss.scan_line(f'TODOIST_TOKEN = "{HEX40}"', "f", 1, [])
    assert [h.rule for h in hits] == ["hex40-near-keyword"]
    hits = ss.scan_line(f"-H 'Authorization: Bearer {HEX40}'", "f", 1, [])
    assert [h.rule for h in hits] == ["hex40-near-keyword"]


def test_hex32_toggl_shape():
    assert ss.scan_line(f"md5 {HEX32}", "f", 1, []) == []
    hits = ss.scan_line(f'"TOGGL_API_TOKEN": "{HEX32}"', "f", 1, [])
    assert "hex32-near-keyword" in [h.rule for h in hits]


def test_placeholders_and_pragma_pass():
    assert ss.scan_line('client_secret = "<your-client-secret-here>"', "f", 1, []) == []
    assert ss.scan_line('api_key: "${TODOIST_API_KEY:-unset-value}"', "f", 1, []) == []
    line = f'X = "{FAKES["github-classic"]}"  # {ss.ALLOW_PRAGMA}'
    assert ss.scan_line(line, "f", 1, []) == []


def test_known_value_file(tmp_path, monkeypatch):
    kf = tmp_path / "known"
    kf.write_text("Zq9" * 8 + "\n")
    monkeypatch.setenv("SECRET_SCAN_KNOWN_FILES", str(kf))
    known = ss.known_values()
    assert known == ["Zq9" * 8]
    hits = ss.scan_line("token is " + "Zq9" * 8, "f", 1, known)
    assert [h.rule for h in hits] == ["known-value"]


def test_render_never_leaks_the_value():
    v = FAKES["github-classic"]
    out = ss.Hit("github-classic", "a.md", 3, v).render()
    assert v not in out and v[4:12] not in out
    assert out.startswith("a.md:3") and "ghp_…" in out


def test_parse_patch_line_numbers():
    patch = [
        "diff --git a/x.py b/x.py",
        "+++ b/x.py",
        "@@ -0,0 +10,2 @@",
        "+ok = 1",
        f'+t = "{FAKES["slack"]}"',
    ]
    hits = ss.parse_patch(patch, [], False)
    assert [(h.path, h.line, h.rule) for h in hits] == [("x.py", 11, "slack")]


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    _git(r, "config", "core.hooksPath", str(r / ".git" / "hooks"))
    out = subprocess.run(["sh", str(HERE / "install.sh"), str(r)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return r


def test_hook_blocks_secret_and_allows_clean(repo, tmp_path):
    (repo / "clean.py").write_text("print('hi')\n")
    _git(repo, "add", "clean.py")
    ok = _git(repo, "commit", "-qm", "clean", check=False)
    assert ok.returncode == 0, ok.stderr

    v = FAKES["google-oauth-secret"]
    (repo / "leak.json").write_text(json.dumps({"client_secret": v}) + "\n")
    _git(repo, "add", "leak.json")
    bad = _git(repo, "commit", "-qm", "leak", check=False)
    assert bad.returncode != 0
    assert "BLOCKED" in bad.stderr and "leak.json:1" in bad.stderr
    assert v not in bad.stderr + bad.stdout
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"

    alert = json.loads((tmp_path / "alerts.jsonl").read_text().splitlines()[-1])
    assert alert["reason"] == "commit_blocked_secret" and v not in json.dumps(alert)


def test_hook_only_scans_added_lines(repo):
    # A secret already in history must not block unrelated edits to the file.
    v = FAKES["slack"]
    f = repo / "old.txt"
    f.write_text(f"{v}\n")
    _git(repo, "add", "old.txt")
    _git(repo, "commit", "-qm", "seed", "--no-verify")
    f.write_text(f"{v}\nnew line\n")
    _git(repo, "add", "old.txt")
    assert _git(repo, "commit", "-qm", "edit", check=False).returncode == 0


def test_installer_refuses_foreign_hook(tmp_path):
    r = tmp_path / "r2"
    r.mkdir()
    _git(r, "init", "-q")
    hook = r / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    out = subprocess.run(["sh", str(HERE / "install.sh"), str(r)], capture_output=True, text=True)
    assert out.returncode == 1 and "refusing" in out.stderr
    out = subprocess.run(["sh", str(HERE / "install.sh"), "--force", str(r)], capture_output=True, text=True)
    assert out.returncode == 0
    assert "secret-scan hook" in hook.read_text()
    assert list((r / ".git" / "hooks").glob("pre-commit.bak.*"))
