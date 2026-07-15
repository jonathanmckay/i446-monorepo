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


def test_fzf_relies_on_default_mouse_no_invalid_flag():
    # Mouse is ON by default in fzf; scroll navigation just requires NOT passing
    # --no-mouse. This fzf build has no --mouse flag — passing it is an
    # 'unknown option' error that kills fzf and breaks the list pipe (bug
    # 2026-07-15), so neither flag may appear on the fzf command line.
    assert "--no-mouse \\\n" not in SRC, "the --no-mouse flag killed scroll; must be gone"
    assert "--mouse \\\n" not in SRC, "--mouse is an unknown option in this fzf; rely on the default"


def test_alt_scroll_workaround_removed():
    assert "1007" not in SRC, "the alt-scroll (1007) disable workaround must be gone"


def test_mode_resets_reassert_mouse_and_strip_motion():
    # An execute() binding (ctrl-d/v/g, cpap/xk prompts) suspends fzf; on resume
    # fzf does NOT restore its mouse, so scroll died after the first such action
    # (bug 2026-07-15). Every reset must therefore RE-ENABLE fzf's click/scroll
    # mouse (1000h/1006h) — the same enable fzf sends at startup, no motion — and
    # only strip the stray MOTION modes (1002l/1003l). It must never DISABLE mouse.
    assert r"printf '\033[?1002l\033[?1003l\033[?1000h\033[?1006h' > /dev/tty" in SRC
    assert r"\033[?1000l" not in SRC and r"\033[?1006l" not in SRC, "must never disable fzf's mouse"


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
