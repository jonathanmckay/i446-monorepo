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
  - run_ritual's auto branch closes the card WITHOUT stamping ⏱️/✅ or writing
    P — those stay daemon-validated at the boundary.
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


# ── did-fast: auto branch closes card only ───────────────────────────────────

def test_run_ritual_auto_branch_skips_stamp_and_points():
    src = _func_src(DIDFAST, "run_ritual")
    i_auto = src.index('r.get("mode") == "auto"')
    i_stamp = src.index("stamp_emoji")
    i_p = src.index("compute-p")
    # The auto branch must return before both the stamp and the P write.
    assert i_auto < i_stamp and i_auto < i_p
    auto_block = src[i_auto:i_stamp]
    assert "return out" in auto_block, "auto branch must return before stamping"
    # And it still records completed-today so dtd hides the card at once.
    assert "append_names" in auto_block
