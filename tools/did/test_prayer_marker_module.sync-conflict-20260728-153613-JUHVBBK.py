"""Regression: the shared prayer_marker module stamps ☀️ on the current 地支
block, and /ص wires it in.

Bug: /ص (the standalone prayer counter) wrote only the Neon AP column, never the
build-order ☀️ marker that tg-tui reads, so a prayer logged via /ص never showed
up in tg-tui. The fix extracts a shared stamp_prayer_marker() and calls it from
/ص. These tests pin the block math, the stamping behavior, and the /ص wiring.
"""

import textwrap
from datetime import datetime
from pathlib import Path

import prayer_marker as pm


def test_current_block_maps_hours_to_branches():
    # 酉 is 16:00–17:59 — the block in the original bug report ("prayer in 西")
    assert pm.current_block(datetime(2026, 6, 14, 16, 0)) == "酉"
    assert pm.current_block(datetime(2026, 6, 14, 17, 30)) == "酉"
    assert pm.current_block(datetime(2026, 6, 14, 4, 0)) == "卯"
    assert pm.current_block(datetime(2026, 6, 14, 20, 0)) == "亥"
    # Clamp below/above the 卯..亥 range rather than indexing out of bounds
    assert pm.current_block(datetime(2026, 6, 14, 2, 0)) == "卯"
    assert pm.current_block(datetime(2026, 6, 14, 23, 0)) == "亥"


def _fixture(tmp_path):
    bo = tmp_path / "build-order.md"
    bo.write_text(textwrap.dedent("""\
        ## 0₲

        - [ ] some goal

        ## -1₲

        - 卯
            - [ ]
        - 酉 ✅ 🎯 📧
            - [ ] 5 more tasks {10}
        - 戌
            - [ ]
    """))
    return bo


def test_stamp_adds_marker_to_current_block(tmp_path):
    bo = _fixture(tmp_path)
    changed = pm.stamp_prayer_marker(bo, now=datetime(2026, 6, 14, 16, 30))  # 酉
    assert changed is True
    out = bo.read_text()
    assert "- 酉 ✅ 🎯 📧 ☀️" in out          # appended, existing markers preserved
    assert out.count("☀️") == 1               # only the current block
    assert "- 卯\n" in out and "- 戌\n" in out  # other blocks untouched


def test_stamp_is_idempotent(tmp_path):
    bo = _fixture(tmp_path)
    now = datetime(2026, 6, 14, 16, 30)
    assert pm.stamp_prayer_marker(bo, now=now) is True
    assert pm.stamp_prayer_marker(bo, now=now) is False  # second call: no change
    assert bo.read_text().count("☀️") == 1


def test_stamp_on_bare_header(tmp_path):
    bo = tmp_path / "build-order.md"
    bo.write_text("## -1₲\n\n- 酉\n    - [ ] \n")
    assert pm.stamp_prayer_marker(bo, now=datetime(2026, 6, 14, 16, 30)) is True
    assert "- 酉 ☀️" in bo.read_text()


def test_no_section_is_noop(tmp_path):
    bo = tmp_path / "build-order.md"
    bo.write_text("# build order\n\n- 酉\n")
    assert pm.stamp_prayer_marker(bo, now=datetime(2026, 6, 14, 16, 30)) is False
    assert "☀️" not in bo.read_text()


def test_stamp_is_section_scoped(tmp_path):
    # A '- 酉' line OUTSIDE the -1₲ section must not be stamped.
    bo = tmp_path / "build-order.md"
    bo.write_text(textwrap.dedent("""\
        ## notes

        - 酉 mention in prose

        ## -1₲

        - 酉
            - [ ]
    """))
    assert pm.stamp_prayer_marker(bo, now=datetime(2026, 6, 14, 16, 30)) is True
    out = bo.read_text()
    assert "- 酉 mention in prose" in out      # prose line untouched
    assert out.count("☀️") == 1                # only the -1₲ block stamped


def test_salah_skill_invokes_prayer_marker():
    """/ص must call prayer_marker.py — the regression that started this bug was
    /ص writing only Neon AP and never the build-order ☀️."""
    skill = Path.home() / ".claude/skills/ص/SKILL.md"
    text = skill.read_text()
    assert "prayer_marker.py" in text, "/ص must invoke prayer_marker.py to stamp ☀️"
