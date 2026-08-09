"""Regression tests for scripts/dream-alert.sh.

Bug (v52 code-review): dream-alert.sh interpolated `$DETAIL` directly into a
`printf` JSON template and an `osascript "display notification ..."` string.
A detail containing a double quote, backslash, or newline (all realistic in
error messages that reference commands) would produce malformed JSONL,
breaking the dashboard's alert-rail parser for every subsequent alert.

Fix: JSON encode via python3 -c; escape the osascript literal with a helper.
"""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path

ALERT = Path(__file__).parent / "dream-alert.sh"


def _run(tmpdir: Path, reason: str, detail: str) -> Path:
    env = os.environ.copy()
    env["HOME"] = str(tmpdir)
    r = subprocess.run(
        ["bash", str(ALERT), reason, detail],
        env=env, capture_output=True, text=True, timeout=10,
    )
    # osascript will fail silently in headless test env — that's fine
    assert r.returncode == 0, r.stderr
    return tmpdir / "vault/z_ibx/alerts.jsonl"


def test_alert_jsonl_is_valid_with_double_quote(tmp_path):
    p = _run(tmp_path, "auth_fail", 'command was "security find-generic-password -w"')
    lines = p.read_text().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])  # must parse
    assert obj["reason"] == "auth_fail"
    assert 'security find-generic-password' in obj["detail"]


def test_alert_jsonl_is_valid_with_backslash(tmp_path):
    p = _run(tmp_path, "path_bad", r"missing C:\Users\jm\keychain.db")
    obj = json.loads(p.read_text().splitlines()[0])
    assert "keychain.db" in obj["detail"]
    assert "\\" in obj["detail"]


def test_alert_jsonl_is_valid_with_newline(tmp_path):
    p = _run(tmp_path, "multiline", "line one\nline two")
    obj = json.loads(p.read_text().splitlines()[0])
    assert obj["detail"] == "line one\nline two"


def test_alert_writes_failed_marker_and_brief_stub(tmp_path):
    # Create a fake dream-run dir so the FAILED marker + stub brief path fires.
    run_dir = tmp_path / "vault/i447/i446/dream-runs/2026.07.01-v99"
    run_dir.mkdir(parents=True)
    _run(tmp_path, "keychain_locked", 'need to unlock "login" keychain')
    assert (run_dir / "FAILED").exists()
    assert (run_dir / "morning-brief.md").exists()
    assert 'keychain_locked' in (run_dir / "FAILED").read_text()
