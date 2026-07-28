"""Regression test: janus must claim the terminal mouse mode.

Bug (2026-06-27): Application(...) omitted mouse_support, defaulting to False.
In full_screen mode the mouse-tracking mode left enabled by the spawning app
(Claude Code / cmux) leaked raw SGR wheel packets into the input buffer on
scroll — a stream of junk characters. Owning the mouse (mouse_support=True),
like fzf and Claude Code do, makes prompt_toolkit enable/parse/disable mouse
mode and consume wheel events instead of leaking them.
"""
import importlib.util
import sys
from pathlib import Path

from prompt_toolkit.filters import to_filter

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_mouse", HERE / "janus.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["janus_mouse"] = m
    spec.loader.exec_module(m)
    return m


def test_mouse_support_enabled():
    m = _load()
    # The Application must own the mouse so scroll wheel events are consumed,
    # not leaked into the query buffer as junk characters.
    assert to_filter(m.app.mouse_support)(), "janus Application must set mouse_support=True"
