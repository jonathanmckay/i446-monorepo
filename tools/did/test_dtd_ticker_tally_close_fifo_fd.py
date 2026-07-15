#!/usr/bin/env python3
"""Regression: dtd's ticker and day-tally jobs must close their inherited copy
of fd 3 (the FIFO writer) or dtd hangs forever on exit, printing nothing.

Bug (2026-07-15): "dtd isn't telling me anything on exit, and it looks like
it's hanging." Confirmed live: `lsof -p <ticker pid>` showed dtd-ticker.py
holding fd `3w` open on the session's FIFO, and ten `/tmp/dtd-*.fifo` files
had accumulated uncleaned from prior sessions — the exit-time cleanup (which
removes the FIFO) never runs because the process hangs before reaching it.

Root cause: same class as the watcher bug fixed 2026-07-11 (see
test_dtd_watcher_closes_fifo_fd.py), but that fix only covered the auto-reload
watcher. Two OTHER persistent background jobs are also spawned after
`exec 3>"$DTD_FIFO"` and were never patched:

- TICKER_PID: `python3 "$DTD_TICKER" ... &` — a bare external command
  backgrounded directly off the main shell, so python3 inherits fd 3 across
  exec and holds it open for the ticker's entire life (until $DTD_PORT
  vanishes).
- TALLY_PID: `( while [[ -f "$DTD_SESSION" ]]; do ... done ) & ` — a subshell
  that never exec's, so it holds its own inherited copy of fd 3 open for as
  long as $DTD_SESSION exists (i.e. the whole session).

Either one alone keeps the FIFO's writer count above zero after the main
loop's exit-time `exec 3>&-`, so the background worker's `read < "$DTD_FIFO"`
never sees EOF, `kill -0 $WORKER_PID` never goes false, and dtd hangs on exit
having printed nothing (the "Waiting for N task(s)..." line only fires when
tasks are still unprocessed; a caught-up worker still deadlocks silently).

Fix: close fd 3 for both jobs, mirroring the watcher's fix — a `3>&-`
redirection on the ticker's command line (it never runs shell code of its
own, so there's no subshell to put `exec 3>&-` inside), and `exec 3>&-` as
the tally subshell's first statement.
"""
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _ticker_line() -> str:
    start = DTD.index('python3 "$DTD_TICKER"')
    end = DTD.index("\n", start)
    return DTD[start:end]


def _tally_block() -> str:
    start = DTD.index("# Day-tally refresher:")
    end = DTD.index("TALLY_PID=", start)
    return DTD[start:end]


def test_ticker_closes_fd3_before_launch():
    line = _ticker_line()
    assert "3>&-" in line, (
        "the ticker command must close its inherited fd 3 (3>&- before "
        "exec'ing python3) or the ticker process holds the FIFO write end "
        "open for its whole lifetime, blocking the worker's read loop from "
        "ever seeing EOF")


def test_tally_closes_fd3_before_loop():
    block = _tally_block()
    i_open_paren = block.index("(")
    i_close_fd = block.find("exec 3>&-")
    i_while = block.find('while [[ -f "$DTD_SESSION" ]]')
    assert i_close_fd != -1, (
        "the tally subshell must close its inherited fd 3 (exec 3>&-) or it "
        "keeps the FIFO's write end open for as long as $DTD_SESSION exists "
        "(the whole dtd session), blocking the worker's read loop from ever "
        "seeing EOF")
    assert i_open_paren < i_close_fd < i_while, (
        "fd 3 must be closed as the FIRST action inside the tally subshell "
        "(before its polling loop) — closing it later still leaves a window "
        "where the tally job holds an extra writer on the FIFO")


def test_exec_fifo_writer_precedes_ticker_and_tally():
    """Sanity: both jobs must be forked AFTER `exec 3>"$DTD_FIFO"` (else fd 3
    wouldn't be inherited at all and the bug — and this fix — wouldn't apply)."""
    i_exec_open = DTD.index('exec 3>"$DTD_FIFO"')
    i_ticker = DTD.index('python3 "$DTD_TICKER"')
    i_tally = DTD.index("# Day-tally refresher:")
    assert i_exec_open < i_ticker
    assert i_exec_open < i_tally


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
