#!/usr/bin/env python3
"""dtd details screen, split rows (2026-10-22):

1. "It's too wide, I can't see bonus points, make it like 5 char thinner":
   min done · pts done · bonus took 54 columns; fields after the first in a
   row now use their own label width and a 2-col gap, so the bonus field is
   on screen in a ~50-col dtd pane.
2. "I couldn't deselect the 'keep remainder'": only space/x toggled it and
   nothing said so; Enter just moved on. Enter and ←/→ toggle too, the legend
   says so while it is focused, and unchecked shows what saving will do.
"""
from __future__ import annotations

import json
import os
import pty
import re
import select
import struct
import sys
import time
from pathlib import Path

import fcntl
import termios

HERE = Path(__file__).resolve().parent
TASK = {"id": "t1", "content": "write the plan {20} (30) [40]", "labels": ["i9"], "due": "2026-10-22"}


def _run(tmp_path: Path, cols: int, keys: list[bytes]) -> tuple[str, dict | None]:
    state = tmp_path / "state" / "jm" / "task-detail"
    state.mkdir(parents=True)
    (state / "t1.json").write_text(json.dumps({"task": {**TASK, "due": {"date": "2026-10-22"}}, "comments": []}))
    (tmp_path / "cache.json").write_text(json.dumps({"today": [TASK]}))
    out = tmp_path / "o.json"
    env = dict(os.environ, TERM="xterm-256color", XDG_STATE_HOME=str(tmp_path / "state"),
               LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, str(HERE / "edit-form.py"), "--id", "t1",
                                    str(tmp_path / "cache.json"), "--out", str(out), "--focus", "split"], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, cols, 0, 0))
    buf = b""

    def pump(t):
        nonlocal buf
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.03)
            if r:
                try:
                    buf += os.read(fd, 65536)
                except OSError:
                    return
    pump(1.0)
    for k in keys:
        try:
            os.write(fd, k)
        except OSError:
            break
        pump(0.15)
    pump(0.5)
    if os.waitpid(pid, os.WNOHANG) == (0, 0):
        try:
            os.write(fd, b"\x1b")
        except OSError:
            pass
        pump(0.5)
        if os.waitpid(pid, os.WNOHANG) == (0, 0):
            os.kill(pid, 9)
            os.waitpid(pid, 0)
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x1b]*\x1b\\|\x1b[()][A-Z0-9]|\x1b[78=>]", "", buf.decode("utf-8", "replace"))
    return text, (json.loads(out.read_text()) if out.exists() else None)


# done_min, done_pts, done_bonus, then Tab through name, time, points, bonus, due to the checkbox
TO_KEEP = [b"\t", b"3", b"\t", b"5", b"\t", b"2"] + [b"\t"] * 6


def test_bonus_fields_fit_a_51_col_pane(tmp_path):
    text, _ = _run(tmp_path, 51, TO_KEEP)
    assert "bonus 2" in text           # done-so-far bonus value on screen
    # remainder bonus label on screen, its value redrawn to total - done
    # (the pty stream holds incremental redraws, not one rendered frame)
    assert re.search(r"\{N\} bonus .*18", text, re.S)


def test_enter_unchecks_keep_remainder_and_save_closes(tmp_path):
    text, out = _run(tmp_path, 51, TO_KEEP + [b"\r", b"\x13"])
    assert "space/⏎ toggle" in text
    assert "close task after done credit" in text
    assert out and out["split"]["keep"] is False and out["split"]["pts"] == 5 and out["split"]["bonus"] == 2


def test_enter_twice_rechecks(tmp_path):
    _, out = _run(tmp_path, 51, TO_KEEP + [b"\r", b"\r", b"\x13"])
    assert out and out["split"]["keep"] is True
