#!/usr/bin/env python3
"""Regression (2026-08-04): "dtd stopped clearing tasks / another invariant /
it's hanging". Root cause: did-fast runs inside dtd's single-threaded
completion worker, and it had NO wall-clock ceiling. A completion for
"😈 سمش" (and repeatedly the ritual completions after it) blocked forever
inside did-fast -- the whole batch of stuck processes were all jammed on the
same task-queue.lock, whose holder was stalled on an unbounded network call
(urllib's socket timeout does NOT cover DNS/getaddrinfo). Because the worker
is single-threaded, one hung did-fast froze the ENTIRE queue: cards closed
(quick-close is detached) but nothing stamped or scored, and exiting dtd
couldn't drain it either -- 7 completions stuck behind one hang.

Two-part fix:
  1. `_install_watchdog()` at the top of main(): a SIGALRM alarm that aborts
     ANY did-fast invocation after DIDFAST_WATCHDOG_SECS (default 60), so a
     hang becomes a fast non-zero exit the dtd worker logs as "✗ ..." and
     skips -- the queue keeps draining no matter what stalls.
  2. refresh_task_queue(block=True) no longer blocks forever on the lock: it
     polls LOCK_NB up to DIDFAST_LOCK_WAIT_SECS then returns the existing
     cache, so explicit refreshes can't pile up behind a stalled holder
     (observed: 6+ did-fast procs jammed on one lock).
"""
import ast
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC_PATH = HERE / "did-fast.py"
SRC = SRC_PATH.read_text()
TREE = ast.parse(SRC)


def _func(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


# ── Structural ────────────────────────────────────────────────────────────

def test_main_installs_the_watchdog_as_its_first_action():
    main = _func("main")
    assert main is not None, "main() must exist"
    first = main.body[0]
    # first executable statement must be the watchdog install, before any
    # arg parsing or network work can begin.
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call) \
        and getattr(first.value.func, "id", None) == "_install_watchdog", (
        "main() must call _install_watchdog() first so every code path -- "
        "completions AND --refresh-cache -- is bounded")


def test_watchdog_uses_sigalrm_and_hard_exits():
    wd = _func("_install_watchdog")
    assert wd is not None, "_install_watchdog() must exist"
    body = ast.get_source_segment(SRC, wd)
    assert "signal.alarm" in body, "must arm a SIGALRM timer"
    assert "SIGALRM" in body, "must register a SIGALRM handler"
    assert "os._exit(124)" in body, (
        "must hard-exit (os._exit) so the abort can't be swallowed by an "
        "except: block somewhere up the stack")
    assert "DIDFAST_WATCHDOG_SECS" in body, "ceiling must be tunable via env"


def test_refresh_lock_block_path_is_bounded_not_infinite():
    """block=True must NOT do a bare blocking LOCK_EX -- that is what let one
    stalled refresh jam every other refresh behind it indefinitely."""
    rq = _func("refresh_task_queue")
    assert rq is not None
    body = ast.get_source_segment(SRC, rq)
    assert "LOCK_EX | fcntl.LOCK_NB" in body or "fcntl.LOCK_NB" in body, (
        "must acquire non-blocking and poll, not block forever")
    assert "DIDFAST_LOCK_WAIT_SECS" in body, "bounded wait cap must exist"
    # the old unconditional blocking form must be gone
    assert "flags = fcntl.LOCK_EX if block" not in body


# ── Functional: the real watchdog actually fires ──────────────────────────

def test_watchdog_actually_aborts_a_hang_fast():
    """Load the REAL _install_watchdog from did-fast.py, arm it at 1s in a
    child that then sleeps 10s, and confirm the child is killed at ~1s with
    exit code 124 -- i.e. a hang can no longer wedge the caller."""
    harness = r'''
import os, sys, time, importlib.util
spec = importlib.util.spec_from_file_location("didfast", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules["didfast"] = m
spec.loader.exec_module(m)
os.environ["DIDFAST_WATCHDOG_SECS"] = "1"
m._install_watchdog()
time.sleep(10)   # a hang the watchdog must interrupt
sys.exit(0)
'''
    t0 = time.time()
    r = subprocess.run([sys.executable, "-c", harness, str(SRC_PATH)],
                       capture_output=True, text=True, timeout=8)
    dt = time.time() - t0
    assert r.returncode == 124, (
        f"expected watchdog exit 124, got {r.returncode}; stderr={r.stderr!r}")
    assert dt < 3, f"watchdog should fire ~1s, took {dt:.2f}s"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
