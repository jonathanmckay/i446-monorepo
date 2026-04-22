"""Tests for -1g-check.py."""
import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

MOD_PATH = Path(__file__).parent / "-1g-check.py"
spec = importlib.util.spec_from_file_location("_1g_check", MOD_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


# --- current_block ---

@pytest.mark.parametrize("hour,expected", [
    (6,  "卯"),
    (7,  "卯"),
    (8,  "辰"),
    (9,  "辰"),
    (14, "未"),
    (15, "未"),
    (16, "申"),
    (17, "申"),
    (22, "亥"),
    (23, "亥"),
])
def test_current_block_valid(hour, expected):
    branch, _ = mod.current_block(hour)
    assert branch == expected


@pytest.mark.parametrize("hour", [0, 1, 3, 4, 5])
def test_current_block_outside_hours(hour):
    branch, time_str = mod.current_block(hour)
    assert branch is None
    assert time_str is None


# --- read_block_goals ---

def _write_build_order(tmpdir: Path, body: str) -> Path:
    p = tmpdir / "build.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_empty_block_returns_empty_list(tmp_path):
    body = """## -1₲

- 卯
    - [ ]
- 申
    - [ ]
"""
    p = _write_build_order(tmp_path, body)
    assert mod.read_block_goals("申", p) == []


def test_block_with_goals_returns_them(tmp_path):
    body = """## -1₲

- 卯
    - [ ] wake up
- 申
    - [ ] 1. Get excel working
    - [ ] 2. Ship /inbound
"""
    p = _write_build_order(tmp_path, body)
    assert mod.read_block_goals("申", p) == [
        "1. Get excel working",
        "2. Ship /inbound",
    ]


def test_checked_items_still_counted(tmp_path):
    body = """## -1₲

- 申
    - [x] Done thing
    - [ ] Other thing
"""
    p = _write_build_order(tmp_path, body)
    assert mod.read_block_goals("申", p) == ["Done thing", "Other thing"]


def test_whitespace_only_bullets_excluded(tmp_path):
    # Regression: the bug we just fixed — don't count `    - [ ] ` (no content) as "set".
    body = """## -1₲

- 申
    - [ ]
    - [ ] real goal
"""
    p = _write_build_order(tmp_path, body)
    assert mod.read_block_goals("申", p) == ["real goal"]


def test_other_blocks_not_included(tmp_path):
    body = """## -1₲

- 卯
    - [ ] earlier goal
- 申
    - [ ] current goal
- 酉
    - [ ] later goal
"""
    p = _write_build_order(tmp_path, body)
    assert mod.read_block_goals("申", p) == ["current goal"]


def test_section_bounded_by_next_heading(tmp_path):
    body = """## -1₲

- 申
    - [ ] in section

## Something Else

- 申
    - [ ] not in -1₲
"""
    p = _write_build_order(tmp_path, body)
    assert mod.read_block_goals("申", p) == ["in section"]


def test_missing_file_returns_none(tmp_path):
    assert mod.read_block_goals("申", tmp_path / "does-not-exist.md") is None


def test_missing_section_returns_none(tmp_path):
    body = """## 0₲
- [ ] no -1₲ section here
"""
    p = _write_build_order(tmp_path, body)
    assert mod.read_block_goals("申", p) is None
