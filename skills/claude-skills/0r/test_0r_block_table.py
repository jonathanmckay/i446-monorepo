"""Regression tests for the 0r skill's build-order actuals (Output 3).

Bug (2026-11-06/07 nightly /0r, sessions b6736cd6 + 57de9ef4): the skill's
地支 table used the traditional 时辰 clock (卯 = 05-07), while the build order's
**Time** lists (build-order-daemon BRANCH_HOURS) and every other reader use
lib/blocks.py (卯 = 04-06). /0r therefore filed e.g. the 06:36 wake-up under
卯 while the daemon filed it under 辰: every `actual:` list sat one block early.
The skill also named a `## -1₲a` heading that build-order.md does not have
(it is `## -1₲`; -1g already prefix-matches after the same drift).
"""
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SKILL_MD = HERE / "SKILL.md"

sys.path.insert(0, str(REPO / "lib"))
import blocks  # noqa: E402  lib/blocks.py

WAKING = ["卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]


def _skill_table() -> dict[str, tuple[int, int]]:
    text = SKILL_MD.read_text(encoding="utf-8")
    rows = re.findall(r"^\|\s*(\S)\s*\|\s*(\d\d):00-(\d\d):00\s*\|", text, re.M)
    return {b: (int(s), int(e)) for b, s, e in rows}


def _daemon():
    spec = importlib.util.spec_from_file_location(
        "bo_daemon_0r", REPO / "scripts" / "build-order-daemon.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_table_lists_all_nine_waking_blocks_in_order():
    assert list(_skill_table()) == WAKING


def test_table_matches_lib_blocks_schedule():
    table = _skill_table()
    for b in WAKING:
        start = blocks.BLOCK_START[b]
        assert table[b] == (start, start + 2), (
            f"0r SKILL.md {b} = {table[b]}, lib/blocks.py says {start:02d}-{start + 2:02d}")


def test_table_matches_daemon_time_list_routing():
    """Same entry, same block: 0r's table must agree with the daemon's router."""
    daemon = _daemon()
    table = _skill_table()
    for b, (s, e) in table.items():
        for h in range(s, e):
            assert daemon.hour_to_branch(h) == b, (b, h)
    # The two entries from the 11-06 / 11-07 reports.
    assert daemon.hour_to_branch(6) == "辰"   # 06:36 wake up
    assert table["辰"][0] <= 6 < table["辰"][1]


def test_heading_is_the_live_one_and_prefix_matched():
    text = SKILL_MD.read_text(encoding="utf-8")
    out3 = text[text.index("## Output 3"):text.index("## Output 4")]
    assert "`## -1₲` section" in out3
    assert "starting with `## -1₲`" in out3, "must prefix-match the heading"
    assert "not found" in out3, "must error, not write elsewhere, when missing"
    # No instruction may still point at the nonexistent -1₲a section.
    assert not re.search(r"-1₲a (section|blocks)", text)
