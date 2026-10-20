#!/usr/bin/env python3
"""Scroll probe: enable SGR mouse reporting exactly like fzf does, then print the
raw bytes the terminal sends. Run inside the SAME cmux/Ghostty pane where dtd
runs, two-finger scroll a few times, then press q.

Tells us which case we're in:
  \\x1b[<64;..M / \\x1b[<65;..M  -> SGR mouse wheel  (fzf SHOULD scroll; issue is fzf)
  \\x1b[A / \\x1b[B               -> arrow keys        (alt-scroll; fzf should nav)
  \\x1b[200~ ... \\x1b[201~       -> bracketed paste   (that's the query-flood cause)
  (nothing printed on scroll)   -> cmux/Ghostty is NOT forwarding scroll to the app
"""
import os
import sys
import termios
import tty

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
try:
    tty.setraw(fd)
    # mimic fzf: normal + SGR mouse tracking, plus bracketed paste
    sys.stdout.write("\033[?1000h\033[?1006h\033[?2004h")
    sys.stdout.flush()
    sys.stderr.write("\r\n== scroll probe ==\r\nTwo-finger scroll a few times, then press q.\r\n\r\n")
    sys.stderr.flush()
    while True:
        chunk = os.read(fd, 128)
        if not chunk or b"q" in chunk:
            break
        sys.stderr.write(repr(chunk) + "\r\n")
        sys.stderr.flush()
finally:
    sys.stdout.write("\033[?1000l\033[?1006l\033[?2004l")
    sys.stdout.flush()
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stderr.write("\r\n(probe ended)\r\n")
