"""Regression test: creating a new block's -1neon cards must refresh the dtd
task cache, so the rituals reappear at the turn of the block.

Bug (2026-07-03): at a 2h boundary the daemon (run_lock_and_mark →
run_block_ritual_cards) deleted the just-ended block's leftover -1neon cards and
created the new block's set in Todoist, but never rebuilt task-queue.json. dtd
only reloads on a cache-file mtime bump, so the new block's ritual cards did not
appear until the periodic refresh daemon's next cycle (~3min later). Fix:
run_block_ritual_cards shells out to did-fast.py --refresh-cache after the
create/delete, mirroring what /0g and /0t do after mutating tasks.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = (Path(__file__).parent / "build-order-daemon.py").read_text()


def _func_src(name: str) -> str:
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"{name} not found in build-order-daemon.py")


def test_run_block_ritual_cards_refreshes_dtd_cache():
    """The card-lifecycle function must trigger a cache refresh after mutating
    the -1neon card set, so dtd's mtime watcher reloads and shows the new block."""
    body = _func_src("run_block_ritual_cards")
    assert "_refresh_dtd_cache(" in body, (
        "run_block_ritual_cards must call _refresh_dtd_cache after "
        "create/delete so the new block's cards reach dtd at the block turn"
    )


def test_refresh_helper_runs_did_fast_refresh_cache():
    """The helper must actually rebuild the cache via did-fast --refresh-cache
    (the same mechanism /0g and /0t rely on), not just log."""
    body = _func_src("_refresh_dtd_cache")
    assert "--refresh-cache" in body, "_refresh_dtd_cache must invoke did-fast --refresh-cache"
    assert "DID_FAST" in body, "_refresh_dtd_cache must call the did-fast script (DID_FAST)"
    # A refresh failure must never break the fire.
    assert "subprocess.TimeoutExpired" in body, "refresh must not raise on timeout/missing binary"


def test_refresh_called_after_card_mutation_not_before():
    """Order guard: the refresh must come AFTER create_block_rituals, else it
    rebuilds the cache before the new cards exist and they still miss the turn."""
    body = _func_src("run_block_ritual_cards")
    i_create = body.find("create_block_rituals(")
    i_refresh = body.find("_refresh_dtd_cache(")
    assert i_create != -1 and i_refresh != -1, "expected both calls present"
    assert i_refresh > i_create, "refresh must run after the cards are created"
