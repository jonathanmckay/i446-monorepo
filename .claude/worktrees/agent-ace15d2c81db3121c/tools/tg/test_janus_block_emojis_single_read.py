"""Regression (2026-09-02): render_all() is prompt_toolkit's FormattedTextControl
text-getter for main_window, called on every 0.1s refresh_interval tick (up to
10x/sec). It used to call render_habits_today/render_morning/render_evening/
render_focus_compact, each of which independently called _read_block_emojis()
-- a fresh build-order.md disk read + full-file parse -- meaning one render_all()
pass did 4 redundant reads of the same file for the same instant in time. Over
hours this was a real, measured contributor to sustained CPU burn (179 CPU-min
observed since 8:55am on a mostly-idle-ticking TUI). Fix: compute it once in
render_all() and thread it through as a parameter.

This test guards the actual fix (call count), not just that the code still
runs -- the signature-compatibility tests elsewhere would pass even if a
regression reintroduced a second internal _read_block_emojis() call inside
one of the four render functions.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_janus_focus_compact import _load_tui, _setup_common  # noqa: E402


def test_read_block_emojis_called_once_per_render_all(monkeypatch):
    mod = _load_tui()
    _setup_common(mod)
    mod.STATE.day_offset = 0

    calls = []
    real = mod._read_block_emojis
    monkeypatch.setattr(mod, "_read_block_emojis", lambda *a, **k: calls.append(1) or real(*a, **k))

    mod.render_all()

    assert len(calls) == 1, (
        f"_read_block_emojis() called {len(calls)}x in one render_all() pass, "
        "expected exactly 1 -- a render function is re-reading build-order.md "
        "instead of using the bo_emojis parameter passed in from render_all()"
    )
