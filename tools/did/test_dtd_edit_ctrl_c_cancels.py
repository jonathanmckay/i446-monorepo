#!/usr/bin/env python3
"""Regression: ctrl-c on dtd's ctrl-o/ctrl-p edit screen cancels the edit and
returns to the list; it must never take dtd down with it.

Bug (2026-10-17, "ctrl-c from the details dialog on a task goes to the main
screen, not exit all the way"): the edit screen is an fzf `execute()` child.
fzf's Pause() hands the child the tty in cooked mode with ISIG on, and the
line discipline delivers SIGINT to the WHOLE foreground process group -- the
zsh action script AND fzf, its parent. fzf dies, so dtd exits.

Fix (dtd.sh, DTD_EDIT heredoc): undefine the tty intr/quit chars as the
first thing the script does, repeat it on the `stty sane` line (sane resets
intr), and restore them right after the tty drain, which both exit paths
pass through. Since 2026-10-21 the screen is the curses form (edit-form.py):
it runs the tty raw, so a ^C once it is up is a key (cancel, exit 2); the
guard covers the ~50 ms before raw mode and the drain after.

Two layers: structural checks on the generated script's text, and a pty
harness that runs the real guard/restore lines around the real form with a
Python parent in the same process group standing in for fzf (it records
whether it received SIGINT). A control run without the guard proves the
harness reproduces the bug, so a silent harness cannot mask a regression.
"""
import json
import os
import pty
import re
import select
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
DTD = (_HERE / "dtd.sh").read_text()

DRAIN = "while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty"


def _edit_heredoc() -> str:
    m = re.search(r'cat > "\$DTD_EDIT" << EDITEOF\n(.*?)\nEDITEOF\n', DTD, re.S)
    assert m, "DTD_EDIT heredoc not found in dtd.sh"
    return m.group(1)


def _unescape(heredoc_text: str) -> str:
    """What zsh does to an unquoted heredoc: \\$ -> $, \\\\ -> \\, \\` -> `."""
    return re.sub(r"\\([\\$`])", r"\1", heredoc_text)


# ── structural ───────────────────────────────────────────────────────────────

def test_intr_is_undefined_before_the_first_python_step():
    body = _edit_heredoc()
    guard = body.find("stty intr undef quit undef < /dev/tty")
    first_py = body.find("python3")
    assert guard != -1, "edit script no longer undefines the intr char"
    assert guard < first_py, "intr guard must precede the form"


def test_stty_sane_in_edit_script_is_followed_by_intr_undef():
    lines = _edit_heredoc().splitlines()
    idx = [i for i, l in enumerate(lines) if l.startswith("stty sane")]
    assert idx, "edit script lost its stty sane line"
    for i in idx:
        assert lines[i + 1].startswith("stty intr undef quit undef"), lines[i:i + 2]


def test_intr_restored_after_drain_before_cancel_check():
    body = _edit_heredoc()
    drain = body.find(DRAIN)
    restore = body.find("stty intr '^C' quit")
    cancel = body.find('echo "edit cancelled"')
    assert -1 not in (drain, restore, cancel)
    assert drain < restore < cancel


# ── pty harness ──────────────────────────────────────────────────────────────

LONG = "write the quarterly planning memo for the whole org (30) [20]"


def _fixture(tmp_path: Path) -> dict:
    state = tmp_path / "state"
    (state / "jm" / "task-detail").mkdir(parents=True)
    (state / "jm" / "task-detail" / "t1.json").write_text(json.dumps({
        "task": {"id": "t1", "content": LONG, "description": "n", "labels": ["i9"]}, "comments": []}))
    (tmp_path / "cache.json").write_text(json.dumps({"today": [{"id": "t1", "content": LONG, "labels": ["i9"]}]}))
    return dict(os.environ, TERM="xterm-256color", XDG_STATE_HOME=str(state), LANG="en_US.UTF-8",
                LC_ALL="en_US.UTF-8", LINES="40", COLUMNS="100")


def _script(tmp_path: Path, guarded: bool) -> str:
    body = _unescape(_edit_heredoc()).splitlines()
    guard = next(l for l in body if l.startswith("stty intr undef"))
    i = next(i for i, l in enumerate(body) if l.startswith("stty sane"))
    sane = "\n".join(body[i:i + 2]) if guarded else body[i]   # sane + its re-undef
    restore = next(l for l in body if l.startswith("stty intr '^C'")) if guarded else ""
    form = f'python3 "{_HERE / "edit-form.py"}" --id t1 "{tmp_path / "cache.json"}" --out "{tmp_path / "out.json"}" < /dev/tty > /dev/tty'
    return "\n".join([
        "#!/bin/zsh",
        guard if guarded else "",
        sane,
        "sleep 0.6",              # stands in for the startup gap before raw mode
        form,
        "rc=$?",
        "stty -echo < /dev/tty 2>/dev/null || true",
        DRAIN,
        restore,
        "print -- \"AFTER rc=$rc out=$([[ -s " + str(tmp_path / "out.json") + " ]] && echo yes || echo no)"
        " intr=$(stty -a < /dev/tty | grep -o 'intr = [^;]*')\"",
        "",
    ])


PARENT = r"""
import signal, subprocess, sys
got = []
signal.signal(signal.SIGINT, lambda *a: got.append(1))
rc = subprocess.call(["zsh", sys.argv[1]])
print(f"PARENT_SIGINT={len(got)} CHILD_RC={rc}", flush=True)
"""


def _run(tmp_path: Path, guarded: bool, ctrl_c_at: str) -> str:
    """ctrl_c_at: 'gap' (during the sleep, before raw mode) or 'form' (once painted)."""
    env = _fixture(tmp_path)
    script = tmp_path / "edit.zsh"
    script.write_text(_script(tmp_path, guarded))
    parent = tmp_path / "parent.py"
    parent.write_text(PARENT)
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, "-I", str(parent), str(script)], env)
    buf = b""

    def pump(t):
        nonlocal buf
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.02)
            if r:
                try:
                    buf += os.read(fd, 65536)
                except OSError:
                    return
    pump(0.25 if ctrl_c_at == "gap" else 1.4)
    os.write(fd, b"\x03")
    pump(1.5)
    if ctrl_c_at == "gap":
        # the ^C byte was swallowed as a key once the form came up (guarded)
        # or killed everything (control); either way give the form an Esc
        try:
            os.write(fd, b"\x1b")
        except OSError:
            pass            # control run: ^C already took the whole group down (EIO)
        pump(0.8)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    return re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\(B", b"", buf).decode("utf-8", "replace")


@pytest.mark.parametrize("at", ["gap", "form"])
def test_ctrl_c_cancels_the_edit_and_fzf_survives(tmp_path, at):
    out = _run(tmp_path, guarded=True, ctrl_c_at=at)
    assert "PARENT_SIGINT=0" in out, out[-400:]
    assert "CHILD_RC=0" in out, out[-400:]
    assert "AFTER rc=2 out=no" in out, out[-400:]     # the form cancelled, nothing saved
    assert "intr = ^C" in out, out[-400:]            # restored for fzf


def test_control_run_without_the_guard_reproduces_the_bug(tmp_path):
    out = _run(tmp_path, guarded=False, ctrl_c_at="gap")
    assert "PARENT_SIGINT=1" in out, out[-400:]
