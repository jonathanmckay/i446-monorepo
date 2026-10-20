"""Regression (2026-10-05): "I can't assign the 1 f694 task to i9."

Re-projecting a selected Toggl row by typing just "@i9" failed: the edit
parser only recognised "@code" after whitespace, so a bare "@i9" became the
new DESCRIPTION with no project. Separately, "1 f694" never auto-resolved to a
project at all (tg-fast had no shortcode), so the entries started untagged."""
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_bare_code", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_bare_code"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_bare_at_code_sets_project_not_description():
    j = _load_tui()
    assert j._parse_edit_text("@i9") == (None, "i9", None, [])


def test_desc_plus_code_still_parses():
    j = _load_tui()
    assert j._parse_edit_text("1 f694 @i9") == ("1 f694", "i9", None, [])
    assert j._parse_edit_text("1 f694 @i9 1219-1234")[:3] == ("1 f694", "i9", ("1219", "1234"))
    assert j._parse_edit_text("email bob@x.com")[:2] == ("email bob@x.com", None)


def test_f694_autoresolves_to_i9():
    out = subprocess.run([sys.executable, str(HERE / "tg-fast.py"), "--resolve", "1 f694"],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    assert out == "i9"
