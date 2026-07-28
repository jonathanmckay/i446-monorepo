#!/usr/bin/env python3
"""Regression: ALL 5 block rituals exist as -1neon cards, including the auto
pair (-1t ⏱️ / -1l ✅), with the daemon staying their sole evaluator.

Change (2026-07-05): previously the daemon created cards only for the 3 manual
rituals (سمش/-1g/-1ibx); -1t/-1l were invisible daemon-computed markers. Now:
  - create_block_rituals spawns the full 5-card set each block;
  - at block close, an EARNED auto card is completed (counts as a done task)
    and an unearned one deleted — decided by the boundary evaluation (`live`),
    falling back to delete when live is unavailable (never award unconfirmed);
  - ritual_card_tag matches auto cards too, so completing `😈 -1t` in dtd
    routes to run_ritual instead of the generic /did path (which would
    mis-route to the unrelated 0₦ habit named `-1t`);
  - (2026-07-13) run_ritual's auto branch now ALSO stamps ⏱️/✅ (on the
    previous block) and credits P immediately, OR'd with the daemon's
    boundary validation of that same previous block — see
    test_did_ritual_manual_or_auto.py.
"""
import ast
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DAEMON = ROOT / "scripts" / "build-order-daemon.py"
DIDFAST = HERE / "did-fast.py"
sys.path.insert(0, str(ROOT / "lib"))
import neon_blocks as nb  # noqa: E402


def _func_src(path: Path, name: str) -> str:
    src = path.read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} not found in {path.name}")


# ── daemon: card creation covers all rituals ─────────────────────────────────

def test_create_block_rituals_has_no_manual_filter():
    src = _func_src(DAEMON, "create_block_rituals")
    assert 'r.get("mode") != "manual"' not in src, (
        "card creation must no longer skip auto rituals")


# ── daemon: close-if-earned at turnover ──────────────────────────────────────

def test_delete_block_rituals_closes_earned_auto_cards():
    src = _func_src(DAEMON, "delete_block_rituals")
    assert "live" in src and "/close" in src, (
        "earned auto cards must be completed, not deleted")
    # Fail-closed: no live results → delete, never close.
    assert "live is not None" in src


def test_lock_and_mark_passes_live_to_card_lifecycle():
    src = _func_src(DAEMON, "run_lock_and_mark")
    assert "run_block_ritual_cards(hour, dry_run=dry_run, live=live)" in src


# ── neon_blocks: auto cards route back to run_ritual ────────────────────────

def test_ritual_card_tag_matches_auto_cards():
    assert nb.ritual_card_tag("😈 -1t") == "-1t"
    assert nb.ritual_card_tag("😈 -1l") == "-1l"
    assert nb.ritual_card_tag("😈 -1g") == "-1g"   # manual still works


def test_bare_names_without_marker_never_match():
    # `/did -1t` (the 0₦ habit) and plain tasks must NOT be hijacked.
    assert nb.ritual_card_tag("-1t") is None
    assert nb.ritual_card_tag("-1l") is None
    assert nb.ritual_card_tag("time log -1t style") is None


# ── did-fast: auto rituals stamp + credit too (2026-07-13 OR redesign) ───────

def test_run_ritual_auto_branch_falls_through_to_stamp_and_credit():
    # Auto rituals (-1t/-1l) must reach the SAME stamp_emoji + P-credit path as
    # manual ones — no early return that skips them. See
    # test_did_ritual_manual_or_auto.py for the full regression coverage
    # (current-block targeting, guarded recompute P credit).
    src = _func_src(DIDFAST, "run_ritual")
    assert 'if r.get("mode") == "auto":' not in src, (
        "the old auto-mode branch (an `if` that returned before stamping) "
        "must be gone — mode is now just a plain is_auto flag, not a branch")
    i_auto = src.index('r.get("mode") == "auto"')
    i_stamp = src.index("stamp_emoji")
    assert i_auto < i_stamp
