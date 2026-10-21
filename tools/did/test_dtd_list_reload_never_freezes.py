#!/usr/bin/env python3
"""Regression (2026-10-04): "ctrl+d and ctrl+v both don't work for delaying
a task (or at least extremely slow)."

fzf runs reload commands in a BACKGROUND process group. The list script's
tail ran `stty -echo < /dev/tty` and a `read -k` drain on /dev/tty, which
raise SIGTTOU/SIGTTIN there and FROZE the script (ps state T). Plain
reload() hid it because the rows had already streamed. The reload-sync()
added to ctrl-d/ctrl-v/ctrl-k waits for the process to exit, so the picker
appeared only when a later reload killed the frozen one, 1-2s later or not
at all (each re-press re-armed on a task row). Measured on Ix: picker
1.0-2.2s before, 0.24-0.63s after.

Fix (2026-10-04): the stty + drain only ran when the script owned the
terminal (tcgetpgrp == getpgrp); the mouse-mode printf stayed unconditional.
2026-10-21: that gate always said no (fzf 0.72 runs reload AND reload-sync
in a background group), so the stty/drain were dead code costing a python
spawn per reload. Both are gone; the tail is now a plain printf. The
structural test checks nothing tty-mode-changing is left, and the pty test
still proves a background run exits instead of freezing.
Also checks the day-row labels requested the same day.
"""
import os
import pty
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
DRAIN = "while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty"
GUARD = "os.tcgetpgrp(fd) == os.getpgrp()"


def _list_tail() -> str:
    body = DTD[DTD.index("cat > \"$DTD_LIST\" << 'LISTEOF'"):]
    body = body[:body.index("\nLISTEOF\n")]
    return body[body.rindex('" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9"'):]


def test_list_tail_has_no_tty_mode_changes_at_all():
    code = "\n".join(l for l in _list_tail().splitlines() if not l.lstrip().startswith("#"))
    assert "stty" not in code and DRAIN not in code and "read -k" not in code and GUARD not in code, (
        "the list tail runs in fzf's background group: any stty/read on /dev/tty "
        "freezes on SIGTTOU/SIGTTIN, and a pgrp gate is a wasted python spawn per reload")


def test_background_list_tail_exits_instead_of_freezing():
    """Run the real tail in a pty, in a process group that is NOT the
    terminal's foreground group (exactly how fzf runs a reload). Before the
    fix this stopped with SIGTTOU and never exited."""
    tail = _list_tail()
    tail = tail[tail.index("\n") + 1:]          # drop the python invocation line
    tail = tail.replace('"$_tl"', "/dev/null")
    with tempfile.TemporaryDirectory() as d:
        script = Path(d) / "tail.zsh"
        script.write_text("#!/bin/zsh\n" + tail + "\nexit 0\n")
        pid, fd = pty.fork()
        if pid == 0:
            # pty child = session leader + the terminal's foreground group.
            # Run the tail in a NEW group (background), like fzf's reload.
            kid = os.fork()
            if kid == 0:
                os.setpgid(0, 0)
                os.execvp("zsh", ["zsh", str(script)])
            end = time.time() + 5
            while time.time() < end:
                w, st = os.waitpid(kid, os.WNOHANG | os.WUNTRACED)
                if w:
                    if os.WIFSTOPPED(st):
                        os.kill(kid, 9)
                        os._exit(3)                 # frozen by SIGTTOU/SIGTTIN
                    os._exit(0 if os.WIFEXITED(st) and os.WEXITSTATUS(st) == 0 else 4)
                time.sleep(0.05)
            os.kill(kid, 9)
            os._exit(5)                             # hung
        out = b""
        while True:
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        _, status = os.waitpid(pid, 0)
        os.close(fd)
        code = os.WEXITSTATUS(status)
        assert code != 3, "list tail was STOPPED by a tty job-control signal"
        assert code != 5, "list tail hung"
        assert code == 0, f"list tail failed (child code {code})"


def test_day_rows_lead_with_the_typeable_number():
    assert "↻ 0 / next occurrence / one day" in DTD
    for n in ("1天", "2天", "7天"):
        assert f"📅 {n}" in DTD
    for old in ("next occurrence (recurring) · tomorrow (one-off)", "📅 tomorrow{", "📅 in 2 days", "📅 in 1 week"):
        assert old not in DTD, old


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
