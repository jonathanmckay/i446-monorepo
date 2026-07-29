"""Regression test for build_order_heal.py.

Bug (2026-07-29): build-order-daemon.py's run_lock_and_mark imports
`build_order_heal` and calls `.heal(BUILD_ORDER)` before reconciling, to
recover ritual stamps a Syncthing conflict silently demoted to a
`.sync-conflict-*` copy. The module was never actually created, so the
import raised ModuleNotFoundError every fire, silently swallowed by the
surrounding `except Exception`, and healing never ran. A goal set on
Straylight (🎯 + its body text) that lost a race with the Ix daemon's own
write was gone for good: `_block_has_goals` found no text under the block,
so every later reconcile stripped 🎯 and permanently cost those 3 points
(辰 scored 10/13 instead of 13/13 today).

Fix: build_order_heal.py now exists and implements heal(), which unions
missing goal text and daemon markers (🎯/⏱️/✅) from `.sync-conflict-*`
siblings back into the live file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_order_heal as boh

LIVE_BEFORE = """---
title: test
date: 2026-07-29
---

## -1₲

- 辰 ☀️ ⏱️ 📧 ✅ (68min) 😈
    - [ ]
    **Time**
    - 06:50-06:59 wake up @infra
- 巳 ☀️ 🎯 📧
    - [ ]
"""

CONFLICT = """---
title: test
date: 2026-07-29
---

## -1₲

- 辰 ☀️ 🎯 ⏱️ ✅ 📧 (67min) 😈
    - [x] kids and something nice at breakfast {10}
    **Time**
    - 06:50-06:59 wake up @infra
- 巳 ☀️ 🎯 📧
    - [ ]
"""


def _write(tmp_path, live=LIVE_BEFORE, conflict=CONFLICT):
    build_order = tmp_path / "build-order.md"
    build_order.write_text(live, encoding="utf-8")
    conflict_path = tmp_path / "build-order.sync-conflict-20260729-080015-D6XJHOP.md"
    conflict_path.write_text(conflict, encoding="utf-8")
    return build_order


def test_heal_restores_lost_goal_text_and_marker(tmp_path):
    build_order = _write(tmp_path)

    result = boh.heal(build_order)

    healed_text = build_order.read_text(encoding="utf-8")
    assert "kids and something nice at breakfast {10}" in healed_text, (
        "heal() must recover the goal text a conflict copy has and the live file lost"
    )
    assert result["merged"], "heal() must report what it merged"
    added = result["merged"][0]["added"]
    assert "辰:goal-text" in added


def test_heal_restores_goal_marker_alongside_text(tmp_path):
    build_order = _write(tmp_path)
    boh.heal(build_order)
    healed_text = build_order.read_text(encoding="utf-8")

    header_line = next(l for l in healed_text.split("\n") if l.startswith("- 辰"))
    assert "🎯" in header_line, (
        "heal() must also restore the 🎯 marker so _block_has_goals + the "
        "header marker agree, or the next reconcile just strips it again"
    )


def test_block_has_goals_would_fail_before_heal_and_pass_after(tmp_path):
    """Directly reproduces the daemon-side symptom: _block_has_goals(辰) is
    False before healing (permanently stripping 🎯 on reconcile) and True
    after."""
    sys.path.insert(0, str(Path(__file__).parent))
    import importlib
    daemon_spec = importlib.util.spec_from_file_location(
        "build_order_daemon_under_test",
        Path(__file__).parent / "build-order-daemon.py",
    )
    daemon = importlib.util.module_from_spec(daemon_spec)
    build_order = _write(tmp_path)
    daemon.BUILD_ORDER = build_order
    daemon_spec.loader.exec_module(daemon)
    daemon.BUILD_ORDER = build_order  # exec_module re-binds from the real import; pin again

    assert daemon._block_has_goals("辰") is False

    boh.heal(build_order)

    assert daemon._block_has_goals("辰") is True


def test_heal_is_a_noop_without_conflict_files(tmp_path):
    build_order = tmp_path / "build-order.md"
    build_order.write_text(LIVE_BEFORE, encoding="utf-8")

    result = boh.heal(build_order)

    assert result == {"merged": []}
    assert build_order.read_text(encoding="utf-8") == LIVE_BEFORE


def test_heal_never_merges_a_conflict_file_from_a_different_day(tmp_path):
    """2026-07-29 incident: block names (辰/午/申/酉/戌/亥) recur every day, so
    a conflict copy dated a different day than the live file must never be
    merged, even though the block name matches -- otherwise stale goal text
    from a past day gets silently spliced into today's file. Reproduces the
    real corruption: heal() ran against the live vault file and pulled in
    goal text from 07-24/07-26/07-27 conflict copies into today's (07-29)
    辰/酉/戌/亥 blocks purely because the block names matched."""
    build_order = tmp_path / "build-order.md"
    build_order.write_text(LIVE_BEFORE, encoding="utf-8")  # dated 2026-07-29

    old_conflict = CONFLICT.replace("date: 2026-07-29", "date: 2026-07-24")
    conflict_path = tmp_path / "build-order.sync-conflict-20260724-162544-D6XJHOP.md"
    conflict_path.write_text(old_conflict, encoding="utf-8")

    result = boh.heal(build_order)

    assert result == {"merged": []}, "a different-day conflict file must never be merged"
    assert build_order.read_text(encoding="utf-8") == LIVE_BEFORE


def test_heal_never_overwrites_existing_live_goal_text(tmp_path):
    """Union-merge only: if the live copy already has real goal text for a
    block, heal() must not touch it even if a conflict copy differs."""
    live = LIVE_BEFORE.replace(
        "- 辰 ☀️ ⏱️ 📧 ✅ (68min) 😈\n    - [ ]\n",
        "- 辰 ☀️ ⏱️ 📧 ✅ (68min) 😈\n    - [x] already had a real goal {5}\n",
    )
    assert "already had a real goal {5}" in live, "fixture replace() didn't match"
    build_order = _write(tmp_path, live=live)

    boh.heal(build_order)

    healed_text = build_order.read_text(encoding="utf-8")
    assert "already had a real goal {5}" in healed_text
    assert "kids and something nice at breakfast" not in healed_text
