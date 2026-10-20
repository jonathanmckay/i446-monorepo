#!/usr/bin/env python3
"""ix-osa.py — Python wrapper around the ix-osa policy.

Same contract as ix-osa.sh: send AppleScript over ssh to the ix host,
hard-fail if ix is unreachable, never call local osascript.

Usage (library):
    from ix_osa import run
    res = run(script)             # returns CompletedProcess-like obj
    if res.returncode != 0: ...

Usage (CLI):
    python3 ix-osa.py < script.applescript

Exit codes match ix-osa.sh: 0 ok, 2 logic error, 3 ssh transport,
4 usage error.
"""
from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

IX_HOST = os.environ.get("IX_HOST", "ix")
UNREACHABLE_MSG = (
    "ERROR: ix unreachable — write aborted to prevent OneDrive merge "
    "conflict. Restore SSH to ix and retry."
)
# Failed WRITE scripts are appended here for replay by ix-drain-queue.sh.
# Overridable for tests.
QUEUE_PATH = Path(os.environ.get("IX_WRITE_QUEUE",
                                 str(Path.home() / ".claude/ix-write-queue.jsonl")))


def _is_write(script: str) -> bool:
    """Same write-detection as _notify_janus: only mutation verbs count."""
    return "set value" in script or "set formula" in script


def _queue_failed_write(script: str, reason: str) -> None:
    """Durably queue a failed write for ix-drain-queue.sh replay.

    Loss-prevention for the dtd→Neon pipeline: dtd marks a task completed
    optimistically, so a write that dies on an ix transport failure or an
    Excel AppleEvent timeout (-1712) is otherwise lost with no retry
    (2026-07-20: a morning of habit completions never reached the workbook).
    Queueing must never mask the original failure, so all errors are
    swallowed; the caller still sees the failing IxResult."""
    if not _is_write(script):
        return
    try:
        QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.datetime.now().isoformat(),
                 "reason": reason, "script": script}
        with QUEUE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


@dataclass
class IxResult:
    returncode: int
    stdout: str
    stderr: str


def run(script: str, *, timeout: float = 30.0) -> IxResult:
    """Run AppleScript on ix. Never falls back to local osascript."""
    if not script or not script.strip():
        return IxResult(4, "", "ix-osa: empty AppleScript")

    cmd = [
        "ssh",
        "-o", "ConnectTimeout=3",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        IX_HOST,
        "osascript", "-",
    ]
    try:
        proc = subprocess.run(
            cmd, input=script, text=True, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _queue_failed_write(script, "timeout")
        return IxResult(3, "", UNREACHABLE_MSG + " (timeout)")
    except FileNotFoundError:
        return IxResult(3, "", "ix-osa: ssh not found on PATH")

    if proc.returncode == 255:
        _queue_failed_write(script, "unreachable")
        return IxResult(3, proc.stdout, UNREACHABLE_MSG)
    if proc.returncode != 0:
        # Queue only the transient Excel-busy failure (-1712 AppleEvent
        # timeout), not logic errors (bad sheet, date not found) — those
        # would fail identically on replay.
        if "-1712" in (proc.stderr or "") or "AppleEvent timed out" in (proc.stderr or ""):
            _queue_failed_write(script, "appleevent-timeout")
        return IxResult(2, proc.stdout, proc.stderr or "ix-osa: osascript failed")

    first = next((ln for ln in proc.stdout.splitlines() if ln.strip()), "")
    if first.startswith("ERROR:") or first.startswith("ERR:"):
        return IxResult(2, proc.stdout, proc.stderr)

    _notify_janus(script)
    return IxResult(0, proc.stdout, proc.stderr)


def _notify_janus(script: str) -> None:
    """Best-effort SIGUSR1 to janus after a successful Excel *write*.

    Keeps janus's neon status event-driven instead of waiting on its 120s
    ticker. Write detection is by AppleScript verb so read-only scripts
    (e.g. janus's own fetch_points) never signal — that would self-loop.
    A missing pidfile or dead pid is silently ignored.
    """
    if "set value" not in script and "set formula" not in script:
        return
    try:
        pid = int((Path.home() / ".cache" / "janus.pid").read_text().strip())
        os.kill(pid, signal.SIGUSR1)
    except (OSError, ValueError):
        pass


def main() -> int:
    if sys.stdin.isatty():
        print("ix-osa.py: no AppleScript on stdin", file=sys.stderr)
        print("usage: python3 ix-osa.py < script.applescript", file=sys.stderr)
        return 4
    res = run(sys.stdin.read())
    if res.stdout:
        sys.stdout.write(res.stdout if res.stdout.endswith("\n") else res.stdout + "\n")
    if res.stderr:
        sys.stderr.write(res.stderr if res.stderr.endswith("\n") else res.stderr + "\n")
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
