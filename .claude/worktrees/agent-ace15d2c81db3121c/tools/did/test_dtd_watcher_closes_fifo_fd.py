#!/usr/bin/env python3
"""Regression: dtd's auto-reload watcher must close its inherited copy of fd 3
(the FIFO writer) or dtd hangs forever on exit.

Bug (2026-07-11): "dtd hangs when I try and exit." Confirmed live: a prior dtd
session (pid 3250-1783819303-26856) that had processed 17/17 tasks was found
over an hour later still running, its worker blocked in `read` on the FIFO
(no "done" ever written to $DTD_HDR) and two zsh processes still holding the
FIFO open — the worker's read end (fd 0) and, unexpectedly, a SECOND process
holding the WRITE end (fd 3) that was never closed.

Root cause: `exec 3>"$DTD_FIFO"` (near the top of dtd.sh) opens fd 3 as a
persistent writer so brief opens by the enter/done/defer scripts never trip
EOF on the reader mid-session. The auto-reload watcher subshell is forked
LATER, after that exec, so it inherits its own independent copy of fd 3 and
never closes it. On exit the main loop does `exec 3>&-` to close ITS copy and
signal EOF to the background worker's `read < "$DTD_FIFO"` loop — but the
watcher's inherited copy keeps the FIFO's writer count above zero, so the
worker never sees EOF and blocks in read() forever. Since `kill
"$WATCHER_PID"` only runs AFTER the exit-time `while kill -0 $WORKER_PID; do
sleep 1; done` wait loop, this is a permanent deadlock whenever the session
completed at least one task (session_count > 0).

Fix: the watcher subshell explicitly closes fd 3 (`exec 3>&-`) as its very
first action, before its port-wait loop, so it never holds an extra writer on
the FIFO.
"""
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _watcher_block() -> str:
    start = DTD.index("# Auto-reload watcher:")
    end = DTD.index("WATCHER_PID=", start)
    return DTD[start:end]


def test_watcher_closes_fd3_before_port_wait():
    block = _watcher_block()
    i_open_paren = block.index("(")
    i_close_fd = block.find("exec 3>&-")
    i_port_wait = block.find('[[ -f "$DTD_PORT" ]] && break')
    assert i_close_fd != -1, (
        "watcher subshell must close its inherited fd 3 (exec 3>&-) or it "
        "keeps the FIFO's write end open for its whole lifetime, blocking "
        "the worker's read loop from ever seeing EOF")
    assert i_open_paren < i_close_fd < i_port_wait, (
        "fd 3 must be closed as the FIRST action inside the watcher subshell "
        "(before the port-wait loop) — closing it later still leaves a "
        "window where the watcher holds an extra writer on the FIFO")


def test_exec_fifo_writer_precedes_watcher():
    """Sanity: the watcher must be forked AFTER `exec 3>"$DTD_FIFO"` (else fd 3
    wouldn't be inherited at all and the bug — and this fix — wouldn't apply)."""
    i_exec_open = DTD.index('exec 3>"$DTD_FIFO"')
    i_watcher = DTD.index("# Auto-reload watcher:")
    assert i_exec_open < i_watcher


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
