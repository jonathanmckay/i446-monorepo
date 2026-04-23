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

import os
import subprocess
import sys
from dataclasses import dataclass

IX_HOST = os.environ.get("IX_HOST", "ix")
UNREACHABLE_MSG = (
    "ERROR: ix unreachable — write aborted to prevent OneDrive merge "
    "conflict. Restore SSH to ix and retry."
)


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
        return IxResult(3, "", UNREACHABLE_MSG + " (timeout)")
    except FileNotFoundError:
        return IxResult(3, "", "ix-osa: ssh not found on PATH")

    if proc.returncode == 255:
        return IxResult(3, proc.stdout, UNREACHABLE_MSG)
    if proc.returncode != 0:
        return IxResult(2, proc.stdout, proc.stderr or "ix-osa: osascript failed")

    first = next((ln for ln in proc.stdout.splitlines() if ln.strip()), "")
    if first.startswith("ERROR:") or first.startswith("ERR:"):
        return IxResult(2, proc.stdout, proc.stderr)

    return IxResult(0, proc.stdout, proc.stderr)


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
