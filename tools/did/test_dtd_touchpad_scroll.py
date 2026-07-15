#!/usr/bin/env python3
"""Regression: touchpad/wheel scroll must SCROLL the list (like Claude Code),
not flood the fzf query — while typing still filters.

History:
- bug 2026-07-05: with mouse on, SGR *motion* events (ESC[<34;x;yM) leaked into
  the query. Worked around with --no-mouse.
- bug 2026-07-14/15: with --no-mouse, the terminal turned two-finger scroll into
  arrow-key bursts (ESC[A/ESC[B) that flooded the query; first patched by
  disabling alternate-scroll (1007), which stopped the flood but killed scroll.
- fix (2026-07-15): use --mouse so fzf CONSUMES scroll natively. fzf subscribes
  to click + SGR scroll (1000/1006) only, never motion (1002/1003); the reset
  before/after actions strips only the stray MOTION modes a child can leave on,
  so it never disables fzf's own scroll.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def test_fzf_uses_mouse_so_scroll_navigates():
    assert "--mouse \\\n" in SRC, "fzf must run with --mouse (so it consumes scroll)"
    assert "--no-mouse \\\n" not in SRC, "the --no-mouse flag killed scroll; must be gone"


def test_alt_scroll_workaround_removed():
    assert "1007" not in SRC, "the alt-scroll (1007) disable workaround must be gone"


def test_mode_resets_are_motion_only():
    # every terminal-mode reset strips ONLY motion (1002/1003) — never fzf's own
    # click/scroll (1000/1006), which would break scrolling after an action.
    assert r"printf '\033[?1002l\033[?1003l' > /dev/tty" in SRC
    assert r"\033[?1000l" not in SRC and r"\033[?1006l" not in SRC


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
