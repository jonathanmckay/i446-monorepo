#!/usr/bin/env python3
"""winsize-guard.py <cmd> [args...] — run cmd on a pty that never sees a 0-size window.

Why (2026-10-06): mosh-server 1.4.0 dies the instant its client reports a
window with 0 rows or 0 cols (reproduced: 0x100 and 40x0 kill it, 1x1 is
fine). The tmux session on Ix survives, but the local mosh-client keeps
talking to a dead server: a frozen screen until JM quits and relaunches dtd /
janus. cmux shrinks panes to zero around sleep/wake, so this hit after every
sleep. This proxy sits between the cmux pane and mosh, forwards bytes both
ways, and passes resizes through only when both dimensions are non-zero;
zero-size resizes are dropped (the last good size stays) and logged to
~/.local/state/jm/ix-tui.log.

Exit status is the child's.
"""
import errno
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
import tty
from pathlib import Path

LOG = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "jm" / "ix-tui.log"
FALLBACK = (24, 80)


def log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {msg}\n")
    except OSError:
        pass


def get_size(fd: int):
    try:
        rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
        return rows, cols
    except OSError:
        return 0, 0


def set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def usable(size) -> bool:
    return size[0] > 0 and size[1] > 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: winsize-guard.py <cmd> [args...]", file=sys.stderr)
        return 2
    stdin = sys.stdin.fileno()
    start = get_size(stdin)
    good = start if usable(start) else FALLBACK
    if not usable(start):
        log(f"start size {start[0]}x{start[1]} unusable, using {good[0]}x{good[1]}")

    pid, master = pty.fork()
    if pid == 0:
        try:
            set_size(0, *good)  # before exec, so the child's first read is sane
            os.execvp(sys.argv[1], sys.argv[1:])
        finally:
            os._exit(127)
    set_size(master, *good)

    def on_winch(_sig, _frm):
        nonlocal good
        size = get_size(stdin)
        if usable(size):
            if size != good:
                good = size
                set_size(master, *size)  # kernel delivers SIGWINCH to the child
        else:
            log(f"dropped {size[0]}x{size[1]} resize (kept {good[0]}x{good[1]}) for {' '.join(sys.argv[1:4])}")

    signal.signal(signal.SIGWINCH, on_winch)
    saved = None
    if os.isatty(stdin):
        saved = termios.tcgetattr(stdin)
        tty.setraw(stdin)
    try:
        fds = [stdin, master]
        while True:
            try:
                ready, _, _ = select.select(fds, [], [])
            except InterruptedError:
                continue
            if master in ready:
                try:
                    data = os.read(master, 65536)
                except OSError as e:
                    if e.errno == errno.EIO:  # child closed the pty
                        break
                    raise
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
            if stdin in ready:
                data = os.read(stdin, 65536)
                if not data:
                    fds = [master]  # stdin closed; keep draining child output
                    continue
                os.write(master, data)
    finally:
        if saved is not None:
            termios.tcsetattr(stdin, termios.TCSAFLUSH, saved)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (status >> 8)


if __name__ == "__main__":
    sys.exit(main())
