"""Regression tests for the 🔒 attestation marker (2026-07-30).

User request: "I should have gotten 13 points for -1n in 辰" — the rituals
were done but no goal text was on file, so the reconcile's live check kept
stripping 🎯 and re-SETting P to 7. A 🔒 on the block header means the user
vouches for every stamp on that line: score them verbatim, never strip.
"""
import importlib.util
import pathlib
import sys

SRC = pathlib.Path(__file__).parent / "build-order-daemon.py"


def _load():
    spec = importlib.util.spec_from_file_location("bod_attest", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bod_attest"] = mod
    spec.loader.exec_module(mod)
    return mod


def _build_order(tmp_path, chen_line):
    p = tmp_path / "build-order.md"
    p.write_text(
        "# build order\n\n## -1₲\n\n"
        "- 卯 😈\n    - [ ] \n"
        f"{chen_line}\n    - [ ] \n"
        "- 巳 ✅ ⏱️\n    - [ ] \n",
        encoding="utf-8",
    )
    return p


LIVE_ALL_FALSE = {"🎯": False, "⏱️": False, "✅": False}


def test_locked_line_trusts_goal_marker_despite_live():
    mod = _load()
    line = "- 辰 ☀️ 🎯 ⏱️ ✅ 📧 🔒 (67min) 😈"
    assert mod._marker_earned(mod.GOAL_MARKER, line, LIVE_ALL_FALSE)


def test_unlocked_line_still_trusts_goal_marker_on_live():
    """2026-08-11 per JM: '-1g should always give me the points and audit
    should not revoke them.' 🎯 is trusted on header presence alone, lock
    or no lock — this test used to assert the OLD pre-2026-08-11 behavior
    (live-gated, stripped without a lock) and had been red ever since that
    change shipped without ever being updated to match."""
    mod = _load()
    line = "- 辰 ☀️ 🎯 ⏱️ ✅ 📧 (67min) 😈"
    assert mod._marker_earned(mod.GOAL_MARKER, line, LIVE_ALL_FALSE)


def test_locked_block_scores_full_13(tmp_path):
    mod = _load()
    mod.BUILD_ORDER = _build_order(tmp_path, "- 辰 ☀️ 🎯 ⏱️ ✅ 📧 🔒 (67min) 😈")
    assert mod.score_block_from_emojis("辰", live=LIVE_ALL_FALSE) == 13


def test_strip_skips_locked_block(tmp_path):
    mod = _load()
    bo = _build_order(tmp_path, "- 辰 ☀️ 🎯 ⏱️ ✅ 📧 🔒 (67min) 😈")
    mod.BUILD_ORDER = bo
    mod._strip_unearned_markers("辰", LIVE_ALL_FALSE)
    assert "🎯" in bo.read_text(encoding="utf-8"), "locked stamps must never be stripped"


def test_strip_leaves_goal_marker_on_unlocked_block(tmp_path):
    """2026-08-11 per JM (same change as _marker_earned above):
    DAEMON_OWNED_MARKERS was emptied so _strip_unearned_markers never claws
    back 🎯, lock or no lock. This test used to assert the OLD strip
    behavior and had been red since that change, never updated to match."""
    mod = _load()
    bo = _build_order(tmp_path, "- 辰 ☀️ 🎯 ⏱️ ✅ 📧 (67min) 😈")
    mod.BUILD_ORDER = bo
    mod._strip_unearned_markers("辰", LIVE_ALL_FALSE)
    chen = next(l for l in bo.read_text(encoding="utf-8").split("\n")
                if l.startswith("- 辰"))
    assert "🎯" in chen


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
