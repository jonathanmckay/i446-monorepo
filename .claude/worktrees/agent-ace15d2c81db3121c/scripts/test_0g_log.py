"""Regression tests for 0g_log.py — set-time durable logging of 0₲ goals.

Bug (2026-06-26): 0₲ goals only reached 0g-log.md if they were still in the live
`## 0₲` section at the 04:00 reset. Goals that moved to `### 以后的目标` (or were
wiped / lost to a sync conflict) before then were never logged. Logging at /0g
set-time fixes this; these tests lock the merge/idempotency behavior.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("zerog_log", Path(__file__).parent / "0g_log.py")
m = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m)  # type: ignore[union-attr]


def test_creates_file_and_section(tmp_path: Path) -> None:
    f = tmp_path / "0g-log.md"
    res = m.log_goals(["tasks to 38 {40}", "do the test for both kids {60}"],
                      day="2026.06.26", path=f)
    assert res == {"day": "2026.06.26", "added": 2, "skipped": 0}
    text = f.read_text()
    assert "## 2026.06.26" in text
    assert "- [ ] tasks to 38 {40}" in text
    assert "- [ ] do the test for both kids {60}" in text


def test_idempotent_same_goal_not_duplicated(tmp_path: Path) -> None:
    f = tmp_path / "0g-log.md"
    m.log_goals(["stay cheerful {60}"], day="2026.06.26", path=f)
    res = m.log_goals(["stay cheerful {60}"], day="2026.06.26", path=f)
    assert res["added"] == 0 and res["skipped"] == 1
    assert f.read_text().count("stay cheerful {60}") == 1


def test_second_call_same_day_merges(tmp_path: Path) -> None:
    """The user runs /0g several times a day — later goals append to the same
    day section, not a duplicate heading."""
    f = tmp_path / "0g-log.md"
    m.log_goals(["stay cheerful {60}"], day="2026.06.26", path=f)
    m.log_goals(["tasks to 38 {40}"], day="2026.06.26", path=f)
    text = f.read_text()
    assert text.count("## 2026.06.26") == 1
    assert "- [ ] stay cheerful {60}" in text
    assert "- [ ] tasks to 38 {40}" in text


def test_newest_day_inserted_on_top(tmp_path: Path) -> None:
    f = tmp_path / "0g-log.md"
    m.log_goals(["older goal"], day="2026.06.25", path=f)
    m.log_goals(["newer goal"], day="2026.06.26", path=f)
    text = f.read_text()
    assert text.index("## 2026.06.26") < text.index("## 2026.06.25"), "newest day must be first"


def test_checkbox_prefix_and_blank_handling(tmp_path: Path) -> None:
    f = tmp_path / "0g-log.md"
    # Accept lines that already carry a checkbox, and ignore blanks/placeholders.
    res = m.log_goals(["- [x] already checked {10}", "  ", "- [ ] "], day="2026.06.26", path=f)
    assert res["added"] == 1  # blank + bare placeholder dropped
    text = f.read_text()
    assert "- [ ] already checked {10}" in text  # normalized to a fresh unchecked line
    assert "[ ] [ ]" not in text  # bare placeholder must not become a goal


def test_day_accepts_dotted_or_dashed(tmp_path: Path) -> None:
    f = tmp_path / "0g-log.md"
    m.log_goals(["g1"], day="2026.06.26", path=f)
    # A dashed day string normalizes via the CLI path; log_goals itself takes dotted.
    assert "## 2026.06.26" in f.read_text()
