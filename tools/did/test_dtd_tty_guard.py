#!/usr/bin/env python3
"""Regression: dtd must recover by itself when its tty is left cooked.

Bug (2026-11-19, "dtd froze"): fzf alive and idle, pane tty in canonical mode
(icanon isig -echo, intr/quit undef), so no key reached fzf until Enter. The
cause was not found (see dtd_tty_guard.py), so the fix is a guard: dtd's
watcher notices icanon and POSTs `transform(dtd_tty_guard.py --check ...)`,
which fzf runs from its main loop (never during an execute() child that owns
a cooked tty on purpose).

Real fzf in a pty throughout; the stuck state is forced with tcsetattr.
"""
import json
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import urllib.request
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
GUARD = _HERE / "dtd_tty_guard.py"
DTD = (_HERE / "dtd.sh").read_text()

pytestmark = pytest.mark.skipif(shutil.which("fzf") is None, reason="fzf not installed")


class Fzf:
    """fzf --listen in a pty, with the master fd pumped so it never blocks."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.port_file = tmp / "port"
        self.log = tmp / "stuck.log"
        env = dict(os.environ, TERM="xterm-256color", FZF_DEFAULT_OPTS="",
                   FZF_DEFAULT_COMMAND="printf 'alpha\\nbeta\\ngamma\\n'")
        env.pop("FZF_API_KEY", None)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execvpe("fzf", ["fzf", "--listen",
                               "--bind", f"start:execute-silent(echo $FZF_PORT > {self.port_file})",
                               "--bind", "ctrl-o:execute(sleep 2)"], env)
        self.pump(1.0)
        deadline = time.time() + 5
        while not (self.port_file.exists() and self.port_file.read_text().strip()) and time.time() < deadline:
            self.pump(0.05)
        self.port = self.port_file.read_text().strip()

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.01)
            if r:
                try:
                    os.read(self.fd, 65536)
                except OSError:
                    return

    def attrs(self):
        return termios.tcgetattr(self.fd)

    def cooked(self) -> bool:
        return bool(self.attrs()[3] & termios.ICANON)

    def force_stuck(self) -> None:
        """The 2026-11-19 state: sane + intr/quit undef + -echo, fzf not told."""
        a = self.attrs()
        a[0] |= termios.ICRNL | termios.IXON
        a[1] |= termios.OPOST
        a[3] = (a[3] | termios.ICANON | termios.ISIG | termios.IEXTEN) & ~termios.ECHO
        a[6][termios.VINTR] = b"\xff"
        a[6][termios.VQUIT] = b"\xff"
        termios.tcsetattr(self.fd, termios.TCSANOW, a)

    def post(self, action: str, wait: float = 0.0) -> None:
        """POST without blocking the pump (fzf may hold the reply while busy)."""
        p = subprocess.Popen(["curl", "-s", "-m", "2", "-XPOST", f"localhost:{self.port}", "--data", action],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 3
        while p.poll() is None and time.time() < end:
            self.pump(0.02)
        self.pump(wait)

    def post_check(self, wait: float = 1.0) -> None:
        self.post(f"transform({sys.executable} {GUARD} --check --log {self.log} --id test)", wait)

    def query(self) -> str:
        box = {}

        def fetch():
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}?limit=0", timeout=2) as r:
                box["q"] = json.load(r)["query"]
        import threading
        t = threading.Thread(target=fetch)
        t.start()
        while t.is_alive():
            self.pump(0.02)
        return box.get("q", "<no answer>")

    def type(self, data: bytes, wait: float = 0.4) -> None:
        os.write(self.fd, data)
        self.pump(wait)

    def close(self) -> None:
        try:
            os.kill(self.pid, 9)
        except OSError:
            pass
        for _ in range(300):
            self.pump(0.01)
            try:
                if os.waitpid(self.pid, os.WNOHANG)[0]:
                    break
            except OSError:
                break


@pytest.fixture
def fzf(tmp_path):
    f = Fzf(tmp_path)
    yield f
    f.close()


def test_stuck_cooked_tty_is_healed_and_logged(fzf):
    fzf.type(b"a")
    assert fzf.query() == "a", "harness: fzf is not reading keys"
    fzf.force_stuck()
    fzf.type(b"zz")                       # typed at the frozen screen
    assert fzf.cooked()
    assert fzf.query() == "a", "control: a cooked tty must swallow keys, else this test proves nothing"

    fzf.post_check()

    assert not fzf.cooked(), "guard left the tty cooked"
    a = fzf.attrs()
    assert not a[3] & (termios.ECHO | termios.ISIG), "guard must restore fzf's raw mode, not just clear icanon"
    assert not a[1] & termios.OPOST
    assert fzf.query() == "a", "keys queued while stuck must be dropped, not replayed into fzf"
    fzf.type(b"b")
    assert fzf.query() == "ab", "keys must reach fzf again after the heal"
    rec = fzf.log.read_text()
    assert "stuck: lflags[icanon isig iexten -echo]" in rec
    assert "intr=undef" in rec and "-- processes on the tty" in rec


def test_raw_tty_is_left_alone(fzf):
    fzf.type(b"a")
    fzf.post_check()
    assert not fzf.cooked()
    assert not fzf.log.exists(), "guard logged a healthy tty as stuck"
    assert fzf.query() == "a"


def test_check_never_fires_inside_an_execute_child(fzf):
    """An execute() child owns a cooked tty on purpose (edit form, prompts).
    The POSTed check must wait for fzf's loop, so it only ever sees the tty
    after Resume() has made it raw again."""
    fzf.type(b"\x0f", wait=0.5)           # ctrl-o -> execute(sleep 2)
    assert fzf.cooked(), "harness: tty should be cooked inside execute()"
    fzf.post_check(wait=0.6)
    assert fzf.cooked(), "guard forced raw mode under a running execute() child"
    assert not fzf.log.exists(), "guard logged a legitimate execute() window as stuck"
    fzf.pump(2.5)                         # child exits, queued check runs
    assert not fzf.cooked()
    assert not fzf.log.exists()


def test_make_raw_matches_fzf_resume():
    import importlib.util
    spec = importlib.util.spec_from_file_location("dtd_tty_guard", GUARD)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    cc = [b"\x00"] * 20
    cc[termios.VINTR] = b"\xff"
    cooked = [termios.ICRNL | termios.IXON | termios.BRKINT, termios.OPOST | termios.ONLCR, termios.CS8 | termios.CREAD,
              termios.ICANON | termios.ISIG | termios.IEXTEN | termios.ECHOE, 9600, 9600, cc]
    assert g.is_cooked(cooked)
    raw = g.make_raw(cooked)
    assert not g.is_cooked(raw)
    assert raw[0] == 0 and not raw[1] & termios.OPOST and raw[1] & termios.ONLCR
    assert raw[3] == termios.ECHOE
    assert raw[6][termios.VMIN] == 1 and raw[6][termios.VTIME] == 0
    assert raw[6][termios.VINTR] == b"\xff", "intr must stay undefined (2026-10-22 ^C window)"
    assert cooked[3] & termios.ICANON, "make_raw must not mutate its input"


# ── structural: dtd's watcher actually calls the guard ───────────────────────

def _watcher() -> str:
    m = re.search(r"\n  typeset -A _acq_tried.*?\n\) &>/dev/null &\nWATCHER_PID=", DTD, re.S)
    assert m, "watcher subshell not found in dtd.sh"
    return m.group(0)


def test_watcher_posts_the_guard_when_the_tty_is_cooked():
    w = _watcher()
    assert '== *" icanon "*' in w, "watcher lost its cheap icanon pre-check"
    assert "dtd_tty_guard.py" in DTD
    post = re.search(r'--data "transform\(\$DTD_TTYGUARD\)"', w)
    assert post, "watcher must POST the guard as a transform() so fzf runs it from its own loop"
    assert w.find('" icanon "') < post.start()
    assert "last_ttyguard" in w, "POST must be throttled: a form open for minutes is cooked on every cycle"


def test_guard_command_is_built_before_the_watcher_starts():
    d = DTD.find("\nDTD_TTYGUARD=")
    assert d != -1, "DTD_TTYGUARD is not defined"
    assert d < DTD.find("typeset -A _acq_tried"), "DTD_TTYGUARD must be defined before the watcher forks"
    line = DTD[d:DTD.find("\n", d + 1)]
    for flag in ("--check", "--log", "--timing", "--hdr", "--hdrgen"):
        assert flag in line, f"guard command lost {flag}"
