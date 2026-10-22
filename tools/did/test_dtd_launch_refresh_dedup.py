#!/usr/bin/env python3
"""Regression (2026-09-04): "dtd is hanging on launch".

Root cause: dtd.sh's startup runs FOUR independent staleness guards --
time-based (cache_age), block-aware (stale_block), today-count
(`.today | length`), and due-count (due_today) -- each capable of firing
its own synchronous, foreground `did-fast.py --refresh-cache` call before
fzf ever launches. On a healthy refresh they never double-fire (a
successful refresh clears every condition, and each later guard re-reads
the file/snapshot the previous one just wrote). But on a DEGRADED network
-- slow enough for refresh_task_queue's own timeouts/retries to burn real
time without actually fixing the cache -- all four guards see the same
stale cache and each launches its OWN full refresh attempt in sequence,
stacking their latency. That serial pile-up, not any single hung call, is
what made dtd feel stuck at launch.

Fix: a single `_dtd_refresh_done` flag, set by whichever guard refreshes
first, gates the other three -- at most one synchronous refresh-cache call
per dtd launch.
"""
import re
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()

# Scope to the startup section only (before the background-worker setup) --
# the daemon's own later backgrounded refresh-cache calls (fire-and-forget,
# already async) are a different code path and must not be swept in here.
_START = DTD.index("DTD_CACHE_MAX_AGE=${DTD_CACHE_MAX_AGE:-600}")
_END = DTD.index("# --- Background worker ---")
STARTUP = DTD[_START:_END]

FLAG = "_dtd_refresh_done"


# The actual invocation, not a comment mentioning the flag name.
CALL = 'python3 "$DID_FAST" --refresh-cache >/dev/null 2>&1'


def test_flag_declared_before_any_guard():
    decl_pos = STARTUP.find(f'{FLAG}=""')
    assert decl_pos != -1, f"{FLAG} must be initialized once before the staleness guards"
    first_call_pos = STARTUP.index(CALL)
    assert decl_pos < first_call_pos, f"{FLAG} must be declared before the first refresh-cache call"


def test_every_startup_refresh_call_is_guarded_and_sets_the_flag():
    # 2026-10-22: the age/block guards refresh in the BACKGROUND
    # (_dtd_refresh_bg) and the count guards block via _dtd_refresh_sync,
    # which WAITS on that background refresh instead of starting a second.
    # Still at most one refresh-cache process per launch: two call sites,
    # one per helper, and every guard calls a helper under its flag.
    calls = [m.start() for m in re.finditer(re.escape('python3 "$DID_FAST" --refresh-cache'), STARTUP)]
    assert len(calls) == 2, (
        f"expected 2 refresh-cache call sites (bg + sync helpers), found {len(calls)}")
    bg = STARTUP[STARTUP.index("_dtd_refresh_bg() {"):STARTUP.index("\n}\n", STARTUP.index("_dtd_refresh_bg() {"))]
    sync = STARTUP[STARTUP.index("_dtd_refresh_sync() {"):STARTUP.index("\n}\n", STARTUP.index("_dtd_refresh_sync() {"))]
    assert "&!" in bg and f"{FLAG}=1" in bg and "_dtd_bg_refresh=$!" in bg
    assert 'kill -0 "$_dtd_bg_refresh"' in sync and f"{FLAG}=1" in sync
    # the sync helper only refreshes itself when no background refresh exists
    assert sync.index('if [[ -n "$_dtd_bg_refresh" ]]') < sync.index("--refresh-cache")
    guards = {"_dtd_refresh_bg": f'-z "${FLAG}"', "_dtd_refresh_sync": '-z "$_dtd_refresh_sync_done"'}
    for helper, gate in guards.items():
        uses = [m.start() for m in re.finditer(rf"\n  {helper}\n", STARTUP)]
        assert len(uses) == 2, f"expected 2 guards calling {helper}, found {len(uses)}"
        for pos in uses:
            if_pos = STARTUP.rindex("if [[", 0, pos)
            cond = STARTUP[if_pos:STARTUP.index("]]; then", if_pos)]
            assert gate in cond, f"{helper} guard not gated on {gate}: {cond!r}"


def test_flag_not_reset_between_guards():
    # No guard may clear the flag once set -- that would defeat the dedup.
    assert f'{FLAG}=""' not in STARTUP[STARTUP.index(f'{FLAG}=""') + 1:], \
        f"{FLAG} must only be initialized once, never reset mid-startup"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
