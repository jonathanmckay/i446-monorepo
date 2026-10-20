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
    # Each `--refresh-cache` invocation in the startup section must sit
    # inside an `if` whose condition checks the flag is unset, and the same
    # block must set the flag afterward so later guards see it.
    calls = [m.start() for m in re.finditer(re.escape(CALL), STARTUP)]
    assert len(calls) == 4, (
        f"expected exactly 4 refresh-cache call sites in dtd.sh startup, found {len(calls)} "
        "-- update this test if a guard was added/removed intentionally")
    for pos in calls:
        # Nearest enclosing `if [[ ... ]]; then` above this call.
        if_pos = STARTUP.rindex("if [[", 0, pos)
        cond_end = STARTUP.index("]]; then", if_pos)
        cond = STARTUP[if_pos:cond_end]
        assert f'-z "${FLAG}"' in cond, (
            f"refresh-cache call at offset {pos} is not gated on an unset {FLAG}: {cond!r}")
        # The flag must be set somewhere between this call and the next
        # `fi` closing this block (so a second guard doesn't re-fire).
        fi_pos = STARTUP.index("\nfi", pos)
        block_tail = STARTUP[pos:fi_pos]
        assert f"{FLAG}=1" in block_tail, (
            f"refresh-cache call at offset {pos} never sets {FLAG}=1, so a later "
            "guard could still re-trigger a redundant refresh")


def test_flag_not_reset_between_guards():
    # No guard may clear the flag once set -- that would defeat the dedup.
    assert f'{FLAG}=""' not in STARTUP[STARTUP.index(f'{FLAG}=""') + 1:], \
        f"{FLAG} must only be initialized once, never reset mid-startup"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
