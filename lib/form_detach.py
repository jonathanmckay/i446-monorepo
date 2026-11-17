"""Shared pieces for dtd-launched forms that save in a detached process.

JM 2026-11-17: "move saving the 1xk887 survey (and all surveys for that
matter) to the background so that the UI is more snappy, and then we can
have the bottom notification letting me know when it is saved."

A form opened inline by dtd ($DTD_FORM in tools/did/dtd.sh) holds fzf's
screen until its process exits, so any Excel / Todoist round trip made
before exit is time JM stares at a dead terminal. The fix is the one 0s.py
and -1g-form.py already use: hand the save to a detached child and exit.
This module is what those children need and each form used to re-implement:

  hdr(msg)         write dtd's status line (the header below the prompt) and
                   make fzf repaint it NOW. Nothing watches the header file:
                   fzf re-reads it only on a keypress or a list reload, so a
                   child that just rewrites the file reports to nobody until
                   JM happens to press a key. poke_dtd() POSTs
                   transform-header to fzf's --listen port instead.
  spawn_detached() start a child that survives this process and the tty.
  announce(msg)    the child's FINAL status: waits for the form process to
                   be gone first, so the repaint request reaches an fzf that
                   has the screen back.
  pid_alive()      is the form process still there.

The wrapper exports DTD_HDR_FILE, DTD_PORT_FILE and DTD_HDRGEN_FILE. All of
it is a no-op outside dtd (env unset), so forms behave the same from a shell.
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from pathlib import Path

ENV_HDR = "DTD_HDR_FILE"
ENV_PORT = "DTD_PORT_FILE"
ENV_HDRGEN = "DTD_HDRGEN_FILE"
ENV_PARENT = "FORM_DETACH_PARENT"   # set by spawn_detached: the form's pid


def in_dtd() -> bool:
    return bool((os.environ.get(ENV_HDR) or "").strip())


def poke_dtd(timeout: float = 2.0) -> bool:
    """Ask the running dtd (fzf --listen) to repaint its header. True if the
    POST was accepted. Never raises: a closed dtd, a stale port file or a
    busy fzf just means nobody is looking."""
    port_file = (os.environ.get(ENV_PORT) or "").strip()
    hdrgen = (os.environ.get(ENV_HDRGEN) or "").strip()
    if not port_file or not hdrgen:
        return False
    try:
        port = int(Path(port_file).read_text().strip())
    except (OSError, ValueError):
        return False
    req = urllib.request.Request("http://localhost:%d" % port,
                                 data=("transform-header(%s)" % hdrgen).encode(),
                                 method="POST")
    key = os.environ.get("FZF_API_KEY")
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def hdr(msg: str, poke: bool = True) -> bool:
    """Show `msg` in dtd's status line. `poke=False` only writes the file:
    use it while the form still owns the screen (fzf is inside execute() and
    must not be sent actions), the next repaint picks the text up."""
    path = (os.environ.get(ENV_HDR) or "").strip()
    if not path:
        return False
    try:
        Path(path).write_text(msg.replace("\n", " ") + "\n")
    except OSError:
        return False
    if poke:
        poke_dtd()
    return True


def spawn_detached(argv: list[str], log: Path, env: dict | None = None) -> subprocess.Popen:
    """Start `argv` in its own session with no tty, stdout/stderr appended to
    `log`. Raises OSError if it cannot start; the caller then saves inline."""
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ if env is None else env)
    env[ENV_PARENT] = str(os.getpid())
    with open(log, "ab") as fh:
        return subprocess.Popen(argv, stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
                                start_new_session=True, env=env)


def parent_pid() -> int:
    """The form process that spawned this child (0 if not a spawned child)."""
    try:
        return int(os.environ.get(ENV_PARENT) or 0)
    except ValueError:
        return 0


def announce(msg: str, parent: int | None = None, settle: float = 0.5,
             timeout: float = 10.0) -> bool:
    """The detached child's final status line. While the form is still up,
    fzf is inside execute() and drops a repaint request, and dtd's wrapper
    has not handed the screen back; so wait for the form to exit (it does so
    right after spawning us), give the wrapper `settle` seconds, then write
    and repaint. Progress lines before this use hdr(msg, poke=...)."""
    pid = parent_pid() if parent is None else parent
    end = time.time() + timeout
    while pid and pid_alive(pid) and time.time() < end:
        time.sleep(0.1)
    if settle:
        time.sleep(settle)
    return hdr(msg)


def pid_alive(pid: int) -> bool:
    """False for a missing/zero pid too: kill(0, 0) signals our own process
    group and would report a form that never recorded itself as alive."""
    try:
        if int(pid) <= 0:
            return False
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, ValueError, TypeError):
        return True
    return True
