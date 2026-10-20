#!/usr/bin/env python3
"""dtd ctrl-p (split) asks all its questions in ONE terminal form (2026-10-04,
user request: "put all the questions there at once and I can tab between
them, and add a question: how many points remaining").

Drives split-form.py in a real pseudo-terminal, and checks dtd.sh wires the
form's four answers through, including the points-remaining override.
"""
import os
import pty
import re
import select
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()


def _drive(keys, total="20"):
    out = tempfile.mktemp()
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.chdir(HERE)
        os.execvp(sys.executable, [sys.executable, "split-form.py", "--title", "write memo (30) [20]",
                                   "--total", total, "--out", out])

    def pump(t):
        end, buf = time.time() + t, b""
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    buf += os.read(fd, 65536)
                except OSError:
                    break
        return buf
    screen = pump(1.0)
    for k in keys:
        os.write(fd, k.encode())
        screen += pump(0.15)
    _, st = os.waitpid(pid, 0)
    res = open(out).read().splitlines() if os.path.exists(out) else None
    if res is not None:
        os.unlink(out)
    return os.WEXITSTATUS(st), res, screen


def test_all_four_questions_on_one_screen():
    _, _, screen = _drive(["\x1b"])
    for label in (b"Points done today", b"What did you do", b"What remains", b"Points remaining"):
        assert label in screen, label


def test_tab_between_fields_and_remaining_follows_done():
    code, res, _ = _drive(["5", "\t", "drafted memo", "\t", "finish memo", "\t", "\r"])
    assert code == 0 and res == ["5", "drafted memo", "finish memo", "15"]


def test_typed_remaining_overrides_the_prefill():
    code, res, _ = _drive(["5", "\t", "\t", "\t", "\x15", "8", "\r"])
    assert code == 0 and res == ["5", "", "", "8"]


def test_shift_tab_and_cjk_with_unknown_total():
    code, res, _ = _drive(["3", "\x1b[Z", "\x1b[Z", "完成了一半", "\r"], total="?")
    assert code == 0 and res == ["3", "", "完成了一半", ""]


def test_escape_cancels_and_points_done_is_required():
    assert _drive(["5", "\x1b"])[:2] == (1, None)
    code, res, screen = _drive(["\r", "\x1b"])
    assert b"must be a number" in screen and (code, res) == (1, None)


def test_dtd_wires_form_answers_and_remaining_override():
    body = DTD[DTD.index("cat > \"$DTD_SPLIT\" << 'SPLITEOF'"):]
    body = body[:body.index("\nSPLITEOF\n")]
    assert "split-form.py" in body and "< /dev/tty > /dev/tty" in body
    assert "IFS= read -r remaining_override" in body
    assert '"${remaining_override:-}"' in body
    assert "remaining_pts = int(sys.argv[12].strip())" in body
    assert "_ask " not in body, "the one-question-at-a-time prompts are gone"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
