#!/usr/bin/env python3
"""winsize-guard must never hand its child a 0-row/0-col window (2026-10-06:
mosh-server dies on one, freezing dtd/janus after every sleep), while still
forwarding real resizes."""
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "winsize-guard.py"
CHILD = r"""
import fcntl, signal, struct, sys, termios, time
def show(*_):
    r, c, _, _ = struct.unpack("HHHH", fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8))
    sys.stdout.write(f"SIZE {r}x{c}\n"); sys.stdout.flush()
signal.signal(signal.SIGWINCH, show)
show()
time.sleep(30)
"""


def _read_until(fd, needle, timeout=5.0):
    buf, end = b"", time.time() + timeout
    while time.time() < end:
        if select.select([fd], [], [], 0.1)[0]:
            buf += os.read(fd, 4096)
            if needle.encode() in buf:
                return buf.decode(errors="replace")
    return buf.decode(errors="replace")


def _resize(fd, pid, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    os.kill(pid, signal.SIGWINCH)


def test_zero_sizes_dropped_real_sizes_forwarded(tmp_path):
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
        os.environ["XDG_STATE_HOME"] = str(tmp_path)
        os.execv(sys.executable, [sys.executable, str(GUARD), sys.executable, "-c", CHILD])
    try:
        assert "SIZE 40x100" in _read_until(fd, "SIZE 40x100")
        for rows, cols in [(0, 100), (40, 0), (0, 0)]:
            _resize(fd, pid, rows, cols)
            out = _read_until(fd, "SIZE", timeout=1.0)
            assert "SIZE 0" not in out and "x0\r" not in out, out
        _resize(fd, pid, 30, 90)
        assert "SIZE 30x90" in _read_until(fd, "SIZE 30x90")
        log = (tmp_path / "jm" / "ix-tui.log").read_text()
        assert "dropped 0x100" in log and "dropped 40x0" in log
    finally:
        os.kill(pid, signal.SIGTERM)
        os.waitpid(pid, 0)


def test_unusable_start_size_falls_back(tmp_path):
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        os.environ["XDG_STATE_HOME"] = str(tmp_path)
        os.execv(sys.executable, [sys.executable, str(GUARD), sys.executable, "-c", CHILD])
    try:
        assert "SIZE 24x80" in _read_until(fd, "SIZE 24x80")
    finally:
        os.kill(pid, signal.SIGTERM)
        os.waitpid(pid, 0)
