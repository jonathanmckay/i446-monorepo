"""Regression: on a terminal too short for the whole day, Tab scrolled the
last row into view for 0.3s and then the pane snapped back to the top, so the
bottom entries could not be selected (user report 2026-09-28). prompt_toolkit
pins a Window whose content has no "[SetCursorPosition]" fragment to line 0
on every render; render_all() must therefore always leave a cursor fragment
in the content -- at the selected row while _cursor_marker is live, else at
the current top-of-viewport line so the scroll position holds."""
import ast
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent
MARK = "[SetCursorPosition]"


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_hold_scroll", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_hold_scroll"] = mod
    spec.loader.exec_module(mod)
    return mod


def _line_of_marker(parts):
    line = 0
    for style, text, *_ in parts:
        if MARK in style:
            return line
        line += text.count("\n")
    return None


def _lines(parts):
    return "".join(t for _, t, *_ in parts)


def test_hold_scroll_pins_cursor_to_top_visible_line():
    m = _load_tui()
    parts = [("class:a", "l0\nl1\n"), ("class:b", "l2\n"), ("class:c", "l3\nl4\nl5")]
    out = m._hold_scroll(parts, 3)
    assert _line_of_marker(out) == 3
    assert _lines(out) == _lines(parts)  # text untouched, only split


def test_hold_scroll_splits_fragment_at_line_start():
    m = _load_tui()
    parts = [("class:a", "l0\nl1\nl2\nl3", lambda e: None)]
    out = m._hold_scroll(parts, 2)
    assert _line_of_marker(out) == 2
    # the split halves keep their style AND mouse handler
    styled = [f for f in out if MARK not in f[0]]
    assert all(len(f) == 3 and f[0] == "class:a" for f in styled)
    assert _lines(out) == "l0\nl1\nl2\nl3"


def test_hold_scroll_leaves_selection_marker_alone():
    m = _load_tui()
    parts = [("class:a", "l0\n"), (MARK, ""), ("class:sel", "l1\n"), ("class:a", "l2")]
    assert m._hold_scroll(parts, 2) == parts


def test_hold_scroll_clamps_to_last_line_and_zero():
    m = _load_tui()
    parts = [("class:a", "l0\nl1\nl2")]
    assert _line_of_marker(m._hold_scroll(parts, 99)) == 2
    assert _line_of_marker(m._hold_scroll(parts, 0)) == 0
    assert _line_of_marker(m._hold_scroll(parts, -5)) == 0
    assert _line_of_marker(m._hold_scroll([], 4)) == 0


def test_render_all_returns_through_hold_scroll():
    """Structural: render_all's return must go through _hold_scroll, else a
    marker-less render pins the pane to the top again."""
    tree = ast.parse((HERE / "janus.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_all")
    rets = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert rets, "render_all has no return"
    for r in rets:
        assert isinstance(r.value, ast.Call) and getattr(r.value.func, "id", "") == "_hold_scroll", \
            ast.unparse(r)
