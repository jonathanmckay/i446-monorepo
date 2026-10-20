#!/usr/bin/env python3
"""Regression: dtd's auto-reload watcher must WAIT for fzf's listen port before
entering its poll loop.

Bug (2026-07-07): "new block, but dtd didn't auto refresh with the -1n tasks."
The watcher subshell spawns BEFORE fzf, but its loop condition is
`while [[ -f "$DTD_PORT" ]]` — and the port file is only written by fzf's
`start` binding ~100ms+ after fzf boots. The condition was therefore false on
the first (immediate) check and the watcher exited at birth, EVERY session:
no cache-mtime polling, no block-boundary staggered refreshes, no day-rollover
handling — the session snapshot stayed frozen at its startup mtime while the
live cache moved on. (The ticker never had this bug because it explicitly
waits up to 5s for the port; the watcher lacked the same wait.)

Fix: a bounded port-wait loop (150 × 0.2s = 30s) before the watcher's state
init + poll loop, mirroring the ticker.
"""
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _watcher_block() -> str:
    start = DTD.index("# Auto-reload watcher:")
    end = DTD.index("WATCHER_PID=", start)
    return DTD[start:end]


def test_watcher_waits_for_port_before_loop():
    block = _watcher_block()
    i_wait = block.find('[[ -f "$DTD_PORT" ]] && break')
    i_loop = block.find('while [[ -f "$DTD_PORT" ]]')
    assert i_wait != -1, "watcher must have a port-wait (break-on-exists) loop"
    assert i_loop != -1
    assert i_wait < i_loop, (
        "the port wait must come BEFORE the while loop — checking the loop "
        "condition immediately races fzf's start binding and exits at birth")


def test_port_wait_is_bounded():
    """The wait must terminate if fzf never publishes (died at boot) — a
    bounded for-loop, not `until`/`while ! -f` which would spin forever."""
    block = _watcher_block()
    wait_region = block[:block.index('while [[ -f "$DTD_PORT" ]]')]
    assert "for _w in {1..150}" in wait_region
    assert "sleep 0.2" in wait_region
