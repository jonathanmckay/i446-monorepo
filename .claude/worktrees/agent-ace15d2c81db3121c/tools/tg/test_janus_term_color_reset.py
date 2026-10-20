"""Regression (2026-08-04): "make dtd and janus background the same".

dtd.sh resets the terminal tab color on every launch so a stale color from a
previous session (e.g. an "orange" tool-failure alert) never lingers. janus
had no equivalent, so a fresh janus launch could show a different tab
background than a fresh dtd launch depending on whatever color state the tab
was last left in. janus's main() must reset it too, non-blocking (a
synchronous call would add TTY-walk + AppleScript latency to first paint).
"""
import re
from pathlib import Path

SRC = (Path(__file__).parent / "janus.py").read_text()


def _main_body():
    m = re.search(r'^async def main\(\):\n(.*?)\nif __name__ == "__main__":', SRC, re.S | re.M)
    assert m, "could not find async def main() in janus.py"
    return m.group(1)


def test_main_resets_term_color_on_startup():
    body = _main_body()
    assert 'term-color.sh' in body and '"reset"' in body, (
        "janus's main() must reset the terminal tab color on startup, "
        "matching dtd.sh's own startup reset")


def test_term_color_reset_is_non_blocking():
    body = _main_body()
    m = re.search(r'subprocess\.(Popen|run)\(', body)
    assert m, "expected a subprocess call for the term-color.sh reset"
    call_start = m.start()
    call_end = body.index("\n", body.index('"reset"', call_start))
    call = body[call_start:call_end]
    assert "term-color.sh" in call and '"reset"' in call, (
        f"expected the subprocess call to launch term-color.sh reset, got: {call!r}")
    assert m.group(1) == "Popen", (
        "must use Popen (fire-and-forget), not run (blocking) — the TTY walk "
        "+ AppleScript call must not add latency to janus's first paint")


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
