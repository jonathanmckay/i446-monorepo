#!/usr/bin/env python3
"""Regression: /ate must write hcbi only through ate.py → excel-http daemon.

2026-11-01 (Dream v175 neon-audit): ate SKILL.md still carried a pre-daemon
"Excel host" section telling the model to write via `ssh ix osascript` and to
fall back to LOCAL osascript when ix was unreachable. Any model that followed
it (e.g. `-2n.py`'s background `claude -p /ate`) would write food cells with
no ledger entry; they then surface only as 23:55 reconcile observations and
fail the hcb strict gate. The skill (and its aliases) must point at ate.py
and must not instruct a raw AppleScript write path."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills" / "claude-skills"
FOOD_SKILLS = ["ate", "helpate", "早餐"]
# An instruction to run AppleScript, not a prohibition of it.
RAW_WRITE = re.compile(r"(ssh ix ['\"]?osascript|^\s*osascript\s+(<<|-e)|fall back to local\s+`?osascript)",
                       re.IGNORECASE | re.MULTILINE)


def _src(name):
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def test_food_skills_have_no_raw_applescript_write_path():
    for name in FOOD_SKILLS:
        m = RAW_WRITE.search(_src(name))
        assert not m, f"/{name} SKILL.md instructs a non-daemon write: {m.group(0)!r}"
    assert "tell application \"Microsoft Excel\"" not in _src("ate")


def test_ate_skill_routes_through_ate_py_and_forbids_fallback():
    src = _src("ate")
    assert "tools/ate/ate.py" in src
    host = src.split("## Excel host", 1)[1].split("\n## ", 1)[0]
    assert "daemon" in host and "Never write hcbi with `osascript`" in host


def test_ate_py_has_no_applescript_fallback():
    src = (ROOT / "tools" / "ate" / "ate.py").read_text(encoding="utf-8")
    assert "osascript" not in src and "ix-osa" not in src and "subprocess" not in src
    assert "excel.batch_append(" in src
