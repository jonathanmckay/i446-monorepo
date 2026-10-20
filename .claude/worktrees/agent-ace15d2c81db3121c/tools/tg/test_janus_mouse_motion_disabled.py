"""Regression (bug 2026-09-02): "trying to navigate through the list of time
entries when I'm in the text input box leads to weird results" -- scroll
bursts leaked raw SGR fragments (bare digits/semicolons/M, e.g.
"35;24;29M35;25;30M...") as literal text into the always-focused input box.

Root cause: prompt_toolkit's own Vt100_Output.enable_mouse_support()
(site-packages prompt_toolkit/output/vt100.py) turns on mode 1003 ("mouse-
drag support" per its own comment) alongside 1000/1006 -- xterm ANY-MOTION
tracking, which reports every pixel of cursor movement, not just clicks and
wheel scrolls. janus only ever reads MOUSE_UP/MOUSE_DOWN (click-select, the
swipe gesture), never MOUSE_MOVE, so 1003 is pure overhead that floods stdin
and can outrun the VT100 parser's escape-sequence disambiguation.

Fix: janus.py wraps app.output.enable_mouse_support so that, right after
prompt_toolkit's own call turns 1000/1003/1015/1006 on, it immediately turns
1002/1003 back off -- leaving 1000/1006 (what wheel-scroll actually uses)
untouched.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_mouse", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_mouse"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_app_output_wraps_enable_mouse_support_with_the_no_motion_patch():
    # janus must actually install the wrapper on the app's real output
    # object (not just define it unused) -- this is the wiring that runs
    # every time prompt_toolkit turns mouse support on.
    mod = _load_tui()
    assert mod.app.output.enable_mouse_support is mod._enable_mouse_support_no_motion


def test_no_motion_patch_disables_1002_1003_without_touching_click_or_sgr():
    # The concrete Output class prompt_toolkit auto-selects for app.output
    # depends on whether stdout is a real tty (Vt100_Output) or not
    # (PlainTextOutput, e.g. under pytest) -- irrelevant to what we're
    # verifying, so swap in a minimal stand-in that just records writes.
    # _enable_mouse_support_no_motion looks up `app.output` fresh (not a
    # captured reference), so reassigning mod.app.output before calling it
    # redirects the write there.
    mod = _load_tui()

    written = []

    class _FakeOutput:
        def write_raw(self, data):
            written.append(data)

    mod.app.output = _FakeOutput()
    mod._enable_mouse_support_no_motion()

    # Turns motion tracking off (1002 belt-and-suspenders, 1003 the actual
    # culprit that floods stdin on every mouse twitch) and touches nothing
    # else -- 1000/1006 (click + SGR extended, what wheel-scroll actually
    # uses) are prompt_toolkit's own concern, never disabled here.
    assert "".join(written) == "\x1b[?1002l\x1b[?1003l"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
