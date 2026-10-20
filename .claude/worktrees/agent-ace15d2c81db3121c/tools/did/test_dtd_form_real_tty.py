#!/usr/bin/env python3
"""Regression (2026-10-17): every inline survey form launched from dtd's Enter
router (form.sh wrapper, 2026-10-16) died with
    OSError: [Errno 22] Invalid argument   (asyncio add_reader on fd 0)
because the wrapper ran `python3 form < /dev/tty`. On macOS kqueue refuses
the /dev/tty alias device, and prompt_toolkit's event loop watches stdin. The
wrapper must hand the form the REAL pty device (/dev/ttysNNN), resolved via
`ps -o tty=` because ttyname() of an fd opened on /dev/tty is "/dev/tty"."""
import os
import pty
import re
import selectors
import subprocess
from pathlib import Path

DTD = Path(__file__).with_name("dtd.sh").read_text()


def _form_wrapper() -> str:
    i = DTD.index('cat > "$DTD_FORM" << FORMEOF')
    return DTD[i:DTD.index("FORMEOF", i + 40)]


def test_wrapper_does_not_feed_the_form_from_dev_tty_alias():
    w = _form_wrapper()
    launch = next(l for l in w.splitlines() if 'python3 "\\$form"' in l)
    assert "< /dev/tty" not in launch, launch
    assert '< "\\$_tty"' in launch, launch
    assert "ps -o tty=" in w


def _in_pty(cmd: list[str]) -> str:
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(cmd[0], cmd)
    data = b""
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        data += chunk
    os.waitpid(pid, 0)
    return data.decode().replace("\r", "")


def test_resolution_snippet_yields_a_kqueue_able_device():
    """Run the wrapper's own resolution lines inside a fresh pty with stdin
    redirected from /dev/tty (exactly the form's situation) and check the
    device it picks can be registered with kqueue, while /dev/tty cannot."""
    w = _form_wrapper()
    lines = [l for l in w.splitlines() if l.startswith("_tty=") or l.startswith('[[ "\\$_tty"')]
    assert len(lines) == 2, lines
    snippet = "\n".join(l.replace("\\$", "$") for l in lines)
    out = _in_pty(["zsh", "-c", f'exec < /dev/tty; {snippet}; echo RESOLVED=$_tty'])
    m = re.search(r"RESOLVED=(\S+)", out)
    assert m, out
    dev = m.group(1)
    assert re.fullmatch(r"/dev/ttys\d+", dev), out
    # The device is gone once the pty closes; prove the property on a live pty.
    pid, fd = pty.fork()
    if pid == 0:
        res = []
        for path in ("/dev/tty", os.ttyname(0)):
            try:
                selectors.KqueueSelector().register(os.open(path, os.O_RDWR), selectors.EVENT_READ)
                res.append(f"{path}:ok")
            except OSError as e:
                res.append(f"{path}:errno{e.errno}")
        os.write(1, (" ".join(res) + "\n").encode())
        os._exit(0)
    data = b""
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        data += chunk
    os.waitpid(pid, 0)
    text = data.decode()
    assert "/dev/tty:errno22" in text and re.search(r"/dev/ttys\d+:ok", text), text
