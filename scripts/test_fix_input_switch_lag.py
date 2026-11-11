"""Regression: scripts/fix-input-switch-lag.sh must turn off the macOS
input-source indicator (TSMLanguageIndicatorEnabled=0), the setting behind
the Sonoma+ switch lag that typed "20fe分" for "20分" (2026-11-11).
Runs against a temp defaults domain so Ix's real prefs are untouched."""
import os, subprocess, pathlib, pytest

SCRIPT = pathlib.Path(__file__).with_name("fix-input-switch-lag.sh")
pytestmark = pytest.mark.skipif(not pathlib.Path("/usr/bin/defaults").exists(),
                                reason="macOS `defaults` only")


def run(tmp_path, *args):
    dom = str(tmp_path / "prefs")          # defaults appends .plist
    env = {**os.environ, "INPUT_SWITCH_DOMAIN": dom}
    r = subprocess.run(["bash", str(SCRIPT), *args], env=env,
                       capture_output=True, text=True, timeout=30)
    return r, dom


def read(dom):
    return subprocess.run(["defaults", "read", dom, "TSMLanguageIndicatorEnabled"],
                          capture_output=True, text=True).stdout.strip()


def test_disables_indicator_and_tells_user_to_relog(tmp_path):
    r, dom = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert read(dom) == "0"
    assert "Log out" in r.stdout


def test_idempotent(tmp_path):
    run(tmp_path); r, dom = run(tmp_path)
    assert r.returncode == 0 and read(dom) == "0"


def test_undo_removes_key(tmp_path):
    run(tmp_path); r, dom = run(tmp_path, "--undo")
    assert r.returncode == 0
    assert read(dom) == ""   # key gone


def test_targets_global_domain_by_default():
    src = SCRIPT.read_text()
    assert 'INPUT_SWITCH_DOMAIN:-kCFPreferencesAnyApplication' in src
    assert "TSMLanguageIndicatorEnabled" in src
