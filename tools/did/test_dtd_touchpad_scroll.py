#!/usr/bin/env python3
"""Regression: touchpad scroll must not flood dtd's fzf query with ^[[A^[[B.

Bug (2026-07-15): dtd runs fzf with --no-mouse, so the terminal (cmux/Ghostty)
uses ALTERNATE SCROLL mode (DECSET 1007) — two-finger touchpad scroll is
translated into arrow-key bursts (ESC[A/ESC[B) that landed in the query as
literal text. Fix: disable 1007 both before launch and (re-asserted) once fzf
enters the alternate screen via the start binding.
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def test_alt_scroll_disabled_before_fzf_launch():
    # the pre-launch mouse-mode reset must also turn off alternate scroll (1007)
    assert r"\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?1007l" in SRC


def test_alt_scroll_reasserted_in_start_binding():
    # entering the alt screen can re-enable 1007, so the start bind disables it again
    assert r"start:execute-silent(printf '\\033[?1007l' > /dev/tty;" in SRC


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
