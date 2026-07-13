#!/usr/bin/env python3
"""Regression: every dtd list reload forces a clean redraw (clear-screen).

Bug (2026-07-13): "double render of a few lines." A single fzf session (one port,
one ticker — verified) left GHOST FRAMES: a reload shrank the list from 85→82
items, but fzf didn't clear the vacated rows, so the previous frame bled through
the new one — two match counts (85/85 over 82/82), a duplicated running-timer
footer, and leftover rows (`followup…`, `AoS`).

Fix: append fzf's `clear-screen` action to every reload — in the key bindings AND
in the watcher's background reload POST — so a shrinking list can't leave ghosts.
"""
import re
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def test_every_binding_reload_forces_clear_screen():
    # Each `reload($DTD_RELOAD)` must be immediately followed by +clear-screen.
    reloads = re.findall(r"reload\(\$DTD_RELOAD\)\+([a-z-]+)", DTD)
    assert reloads, "expected DTD_RELOAD bindings"
    bad = [r for r in reloads if r != "clear-screen"]
    assert not bad, f"reload(s) not immediately followed by clear-screen: {bad}"


def test_enough_bindings_covered():
    # 12 key bindings reload the list (enter, alt-enter, ctrl s/d/x/p/v/g/k/z/r/t).
    assert DTD.count("reload($DTD_RELOAD)+clear-screen") >= 12


def test_watcher_background_reload_clears_too():
    # External changes (a completion elsewhere) can also shrink the list, so the
    # watcher's listen-POST reload must clear-screen as well.
    assert "reload($watch_reload)+clear-screen" in DTD
    assert 'reload($watch_reload)" ' not in DTD, "a watcher reload POST lacks clear-screen"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
