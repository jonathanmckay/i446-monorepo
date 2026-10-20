"""Regression tests for the ibx0 key remap + hybrid single-key reader (2026-06-22).

Change: archive moved from 'a' → 'e' (single keypress, no Enter); the old
'e' (expand) moved to 'x'; 'a' is unbound (shows a transition hint). Compose
commands (r/R/p/P/t <text>) and free-text → Claude still use line mode.
"""
import io
import sys
from pathlib import Path

import ibx0

SRC = (Path(__file__).parent / "ibx0.py").read_text()


def _branch(after: str, n: int = 220) -> str:
    i = SRC.index(after)
    return SRC[i:i + n]


def test_archive_bound_to_e_not_a():
    # archive fires on 'e' and calls do_archive
    assert 'elif cmd == "e":' in SRC
    assert "do_archive(item)" in _branch('elif cmd == "e":')
    # 'a' no longer archives — it's an unbound transition hint
    assert "do_archive" not in _branch('elif cmd == "a":')
    assert "archive moved to 'e'" in _branch('elif cmd == "a":')


def test_expand_moved_to_x():
    assert 'elif cmd == "x":' in SRC
    assert "_full_body" in _branch('elif cmd == "x":')


def test_main_loop_uses_single_key_reader():
    assert 'read_command("> ")' in SRC
    assert 'user_input = input("> ")' not in SRC  # old line-input gone


def test_instant_keys_membership():
    # instant (no-Enter) actions include archive(e) and expand(x)
    assert {"e", "x", "d", "s", "o", "c", "f", "q", "a", "?"} <= ibx0.INSTANT_KEYS
    # compose prefixes MUST stay line-mode so their <text> can be typed
    for k in ("r", "R", "p", "P", "t"):
        assert k not in ibx0.INSTANT_KEYS, f"{k!r} must remain line-mode"


def test_read_command_non_tty_falls_back_to_readline(monkeypatch):
    # When stdin isn't a TTY (tests/pipes), read_command reads a whole line so
    # compose commands and free-text still work in non-interactive contexts.
    monkeypatch.setattr(sys, "stdin", io.StringIO("r hello there\n"))
    assert ibx0.read_command("> ") == "r hello there"


def test_read_command_single_key_via_pty():
    # Drive a real PTY so isatty() is true: a single 'e' with NO newline must
    # return immediately as 'e' (the core "no Enter" behavior).
    import os
    import pty
    import signal

    pid, fd = pty.fork()
    if pid == 0:  # child
        try:
            # pytest replaces sys.stdin with a capture object; restore the real
            # PTY slave (fd 0) so read_command's isatty()/termios path runs.
            sys.stdin = os.fdopen(0, "r")
            import ibx0 as _m
            r = _m.read_command("")
            os.write(1, f"RESULT[{r}]".encode())
        except BaseException as e:  # pragma: no cover
            os.write(1, f"ERR[{e}]".encode())
        finally:
            os._exit(0)

    # parent
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(20)
    out = b""
    try:
        os.write(fd, b"e")  # one keypress, no newline
        while b"RESULT[" not in out and b"ERR[" not in out:
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    except TimeoutError:  # pragma: no cover
        pass
    finally:
        signal.alarm(0)
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
    assert b"RESULT[e]" in out, f"expected single-key 'e', got: {out!r}"
