#!/usr/bin/env python3
"""Regression (2026-08-26): an orphaned dtd-ticker.py pinned ~100% CPU for
~3 days. dtd.sh only kills its ticker via an explicit `kill "$TICKER_PID"` at
normal cleanup, which never runs on a crash/force-close, and the existing
fails>30-consecutive-POSTs check didn't save us either: fzf binds an
OS-assigned ephemeral port, so a LATER, unrelated fzf --listen session can be
reassigned the exact same port number, and the stale ticker's change-footer()
POST silently "succeeds" against that wrong picker, resetting `fails` to 0
before it ever crosses the threshold.

Fix: the ticker is dtd.sh's direct child (`python3 "$DTD_TICKER" ... &` in
dtd.sh, TICKER_PID captured right after), not fzf's. If dtd.sh dies for any
reason, the kernel reparents the ticker to launchd and getppid() changes —
a signal with no PID-reuse race, unlike polling an external PID by number.
Plus a flat MAX_RUNTIME ceiling as insurance against any failure mode that
defeats both checks, the way port-reuse defeated fails>30.

These tests spawn a real intermediate "fake dtd.sh" process that backgrounds
the ticker as ITS child (matching dtd.sh's actual launch shape), so killing
that intermediate genuinely orphans/reparents the ticker via the kernel —
not a mock.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TICKER = HERE / "dtd-ticker.py"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def test_ticker_exits_within_seconds_of_parent_dying(tmp_path):
    port_file = tmp_path / "dtd.port"
    port_file.write_text("59999")  # unreachable port: POSTs will fail-fast
    pid_file = tmp_path / "ticker.pid"

    # Mirrors dtd.sh's `python3 "$DTD_TICKER" "$DTD_PORT" ... &` — bash is the
    # ticker's real OS parent here, exactly like dtd.sh is in production.
    fake_parent = subprocess.Popen(
        ["bash", "-c",
         f'python3 "{TICKER}" "{port_file}" & echo $! > "{pid_file}"; wait'],
    )
    try:
        for _ in range(50):  # up to 5s for the ticker to start and record its pid
            if pid_file.exists() and pid_file.read_text().strip():
                break
            time.sleep(0.1)
        ticker_pid = int(pid_file.read_text().strip())
        assert _pid_alive(ticker_pid), "ticker should be running before the test"

        fake_parent.kill()   # orphans the ticker: kernel reparents it to launchd
        fake_parent.wait(timeout=5)

        for _ in range(50):  # should exit within ~1 tick in practice, not MAX_RUNTIME
            if not _pid_alive(ticker_pid):
                break
            time.sleep(0.1)
        else:
            os.kill(ticker_pid, signal.SIGKILL)  # don't leak a process from the test itself
            assert False, (
                "ticker did not exit within 5s of its parent dying — "
                "orphan/reparent detection is not working")
    finally:
        if fake_parent.poll() is None:
            fake_parent.kill()


def test_max_runtime_ceiling_is_defined_and_generous():
    """Sanity bound on the insurance ceiling: present, and well above any real
    dtd session (hours, not minutes) but well below "runs for days" (the bug)."""
    spec_globals = {}
    src = TICKER.read_text()
    exec(compile(src.split("if __name__")[0], str(TICKER), "exec"), spec_globals)
    max_runtime = spec_globals["MAX_RUNTIME"]
    assert 3600 <= max_runtime <= 24 * 3600, (
        f"MAX_RUNTIME={max_runtime}s should be generous-but-bounded (1h-24h range)")


def test_loop_checks_getppid_and_max_runtime():
    """Wiring check: the wait loop must actually reference both new guards
    (a passing behavioral test above could in principle pass for the wrong
    reason if these lines were later refactored away silently)."""
    src = TICKER.read_text()
    assert "os.getppid() != parent_pid" in src
    assert "time.time() - proc_start > MAX_RUNTIME" in src
