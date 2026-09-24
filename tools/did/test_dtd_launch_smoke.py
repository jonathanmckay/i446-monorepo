#!/usr/bin/env python3
"""Launch smoke test: dtd.sh must actually come up and every action script it
generates must be non-empty and parse.

Why (2026-09-24): dtd.sh is a script GENERATOR -- it writes ~18 zsh action
scripts to /tmp via heredocs at startup, and fzf binds keys to those files.
A typo inside an unquoted heredoc (that day: backticks in a comment, which
zsh ran as command substitution) makes the write fail, leaves the file at
0 bytes and dtd never reaches fzf. `zsh -n dtd.sh` cannot catch it: it
parses the outer file and never expands heredoc bodies. Only a real launch
does, so this test sources dtd.sh in a pty, waits for the scripts to appear
and checks them WHILE dtd is alive (it rm -f's them on exit).

Integration test: needs zsh + fzf on PATH and dtd's cache to exist. Skips
cleanly when they do not, never silently passes.
"""
import glob
import os
import pty
import select
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

import pytest

_HERE = Path(__file__).resolve().parent
DTD = _HERE / "dtd.sh"

# Every heredoc-generated action script dtd.sh writes (basename suffixes).
EXPECTED = {
    "agent", "blockapply", "blockarm", "defer", "delete", "domainsearch",
    "done-hide", "done-router", "done", "edit", "enter", "list", "refresh",
    "skip", "split", "start", "undo", "view-toggle",
}


def _wait_for(pattern_fn, timeout: float):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pattern_fn()
        if v:
            return v
        time.sleep(0.1)
    return None


OUT = bytearray()  # everything dtd painted this run, shown on failure


def _drain(fd, seconds: float):
    """Read pty output (keeping it for diagnostics) so the child never blocks
    on a full buffer."""
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if not r:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        OUT.extend(chunk)


def _tail() -> str:
    return "dtd output tail:\n" + bytes(OUT[-1500:]).decode("utf-8", "replace")


def _reap(pid: int, fd: int, timeout: float) -> bool:
    """Poll waitpid(WNOHANG) while draining the pty; True once the child is gone."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if os.waitpid(pid, os.WNOHANG) != (0, 0):
                return True
        except ChildProcessError:
            return True
        _drain(fd, 0.1)
    return False


@pytest.mark.skipif(shutil.which("fzf") is None or shutil.which("zsh") is None,
                    reason="needs zsh + fzf on PATH")
def test_dtd_launches_and_generates_every_action_script():
    before = set(glob.glob("/tmp/dtd-*.start.sh"))
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child
        os.execvp("zsh", ["zsh", "-c", f"source {DTD}"])
    stem = None
    try:
        new = None
        for _ in range(100):
            _drain(fd, 0.1)
            new = set(glob.glob("/tmp/dtd-*.start.sh")) - before
            if new:
                break
        assert new, "dtd never wrote its start.sh -- launch failed before script generation\n" + _tail()
        stem = sorted(new)[0][: -len(".start.sh")]
        # fzf publishes its --listen port on its start binding; once that file
        # is non-empty every heredoc above the fzf call has been written.
        up = False
        for _ in range(250):
            _drain(fd, 0.1)
            if os.path.exists(stem + ".port") and os.path.getsize(stem + ".port") > 0:
                up = True
                break
        if not up:
            empties = sorted(os.path.basename(f) for f in glob.glob(stem + ".*.sh") if os.path.getsize(f) == 0)
            pytest.fail("fzf never came up (no .port file) -- dtd launch failed. "
                        f"0-byte action scripts (a heredoc write failed): {empties}\n" + _tail())
        found = {}
        for path in glob.glob(stem + ".*.sh"):
            name = os.path.basename(path)[len(os.path.basename(stem)) + 1 : -3]
            found[name] = path
        missing = EXPECTED - set(found)
        assert not missing, f"action scripts never generated: {sorted(missing)}"
        empty = [n for n, p in found.items() if os.path.getsize(p) == 0]
        assert not empty, f"0-byte action scripts (heredoc write failed): {sorted(empty)}"
        bad = []
        for n, p in found.items():
            r = subprocess.run(["zsh", "-n", p], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(f"{n}: {r.stderr.strip()}")
        assert not bad, "generated scripts fail zsh -n:\n" + "\n".join(bad)
    finally:
        # Exit the way a user does (ESC aborts fzf, dtd's loop breaks and it
        # rm -f's its own files). Always keep draining the pty while waiting:
        # a child blocked on a full pty output buffer can never finish exiting,
        # and a bare waitpid() then hangs forever (first draft did exactly that).
        try:
            os.write(fd, b"\x1b")
        except OSError:
            pass
        if not _reap(pid, fd, 4.0):
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(os.getpgid(pid), sig)
                except (OSError, ProcessLookupError):
                    pass
                if _reap(pid, fd, 2.0):
                    break
        os.close(fd)
        if stem:  # never leave this session's files behind, whatever exit path ran
            for leftover in glob.glob(stem + ".*"):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
