"""2026-11-01: "did is asking me questions but not enough space". The /done
points question was a footer flash: one line, no wrap, 8s expiry, on a pane
capped at 64 columns -- clipped, then gone while the ❯ box still waited for
the answer. Now it is an ask(): pinned in the footer, wrapping up to
ASK_MAX_LINES, until its target is consumed."""
import ast
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_askfooter", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_askfooter"] = mod
    spec.loader.exec_module(mod)
    return mod


MOD = _load()
LONG = 'points for "1 xk88 weekly marriage review notes (45) [40]"? total (40) or rate (.5/m), ⏎ = 0'


def _clear():
    MOD.STATE.done_target = None
    MOD.STATE.points_edit_target = None
    MOD.STATE.split_target = None
    MOD.STATE.edit_target = None
    MOD.STATE.pending_ask = ""
    MOD.STATE.flash = ""
    MOD.STATE.flash_until = 0.0


def _footer_text():
    return "".join(t for _, t in MOD.render_footer())


def test_ask_outlives_flash_expiry_while_done_target_is_armed():
    _clear()
    MOD.STATE.done_target = {"desc": "x", "start_dt": None, "code": None}
    MOD.ask(LONG)
    # Long after any flash would have expired, the question is still there,
    # whole, and tells the user where to answer.
    MOD.STATE.flash_until = time.monotonic() - 100
    txt = _footer_text()
    assert LONG in txt
    assert "❯" in txt


def test_ask_footer_may_grow_to_fit_but_is_one_line_otherwise():
    _clear()
    assert MOD.footer_height().max == 1
    MOD.STATE.done_target = {"desc": "x", "start_dt": None, "code": None}
    MOD.ask(LONG)
    assert MOD.footer_height().max == MOD.ASK_MAX_LINES > 1
    assert MOD.footer_window.wrap_lines() is True


def test_ask_clears_itself_once_the_target_is_consumed():
    _clear()
    MOD.STATE.done_target = {"desc": "x", "start_dt": None, "code": None}
    MOD.ask(LONG)
    MOD.STATE.done_target = None  # Enter consumed it elsewhere
    assert LONG not in _footer_text()
    assert MOD.HINT_LINE.strip() in _footer_text()
    assert MOD.footer_height().max == 1


def test_live_flash_still_wins_then_question_returns():
    _clear()
    MOD.STATE.done_target = {"desc": "x", "start_dt": None, "code": None}
    MOD.ask(LONG)
    MOD.flash("$ did x 0900-0930", 4.0)
    assert "$ did" in _footer_text()
    MOD.STATE.flash_until = time.monotonic() - 1
    assert LONG in _footer_text()


def test_no_points_question_is_a_plain_flash_anymore():
    """Structural: every 'points for ...' prompt goes through ask(), never
    flash() -- a flash is clipped to one line and times out."""
    tree = ast.parse((HERE / "janus.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "flash" and node.args:
            a = node.args[0]
            src = ast.unparse(a)
            assert "points for" not in src, f"line {node.lineno}: points question via flash(): {src[:60]}"
