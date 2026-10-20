"""Regression (user report 2026-09-29): a fix shipped to janus.py was invisible
for a day because the long-running process never restarts — the red
"⚠ RESTART" header was the only mechanism and it was missed. janus must now
re-exec itself once the file on disk has been newer for a settle window and
the UI is idle (nothing typed, no edit/split armed, no selection, no
recording)."""
import ast
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "janus.py").read_text()
TREE = ast.parse(SRC)


def _load():
    spec = importlib.util.spec_from_file_location("janus_self_restart", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_self_restart"] = mod
    spec.loader.exec_module(mod)
    return mod


def _idle(**over):
    kw = dict(input_text="", edit_target=None, split_target=None, event_sel=None, recording=None)
    kw.update(over)
    return kw


def test_restart_only_after_settle_window_and_when_idle():
    m = _load()
    st = {"want": False, "stale_since": 0.0}
    settle = m.STALE_RESTART_SETTLE_S
    assert m._should_self_restart(True, 100.0, state=st, **_idle()) is False  # first sighting arms the clock
    assert st["stale_since"] == 100.0
    assert m._should_self_restart(True, 100.0 + settle - 1, state=st, **_idle()) is False
    assert m._should_self_restart(True, 100.0 + settle + 1, state=st, **_idle()) is True


def test_not_stale_resets_the_clock():
    m = _load()
    st = {"want": False, "stale_since": 50.0}
    assert m._should_self_restart(False, 500.0, state=st, **_idle()) is False
    assert st["stale_since"] == 0.0


def test_busy_ui_blocks_restart():
    m = _load()
    settle = m.STALE_RESTART_SETTLE_S
    for busy in (dict(input_text="ibx i9 @i9"), dict(edit_target={"ids": [1]}),
                 dict(split_target={"ids": [1]}), dict(recording={"pid": 1})):
        st = {"want": False, "stale_since": 100.0, "sel": None}
        assert m._should_self_restart(True, 100.0 + settle + 60, state=st, **_idle(**busy)) is False, busy


def test_fresh_selection_blocks_but_a_forgotten_one_does_not():
    m = _load()
    settle, sel_idle = m.STALE_RESTART_SETTLE_S, m.STALE_RESTART_SEL_IDLE_S
    st = {"want": False, "stale_since": 100.0, "sel": None}
    t = 100.0 + settle + 1
    # first sighting of a selection: fresh → blocks
    assert m._should_self_restart(True, t, state=st, **_idle(event_sel=("k",))) is False
    assert st["sel"] == (("k",), t)
    # still the same selection, but older than the idle window → no longer blocks
    assert m._should_self_restart(True, t + sel_idle + 1, state=st, **_idle(event_sel=("k",))) is True
    # a DIFFERENT selection restarts the clock → blocks again
    assert m._should_self_restart(True, t + sel_idle + 2, state=st, **_idle(event_sel=("j",))) is False
    # clearing the selection clears the record
    m._should_self_restart(True, t + sel_idle + 3, state=st, **_idle())
    assert st["sel"] is None


def test_main_schedules_the_self_restart_ticker():
    fn = next(n for n in ast.walk(TREE) if isinstance(n, ast.AsyncFunctionDef) and n.name == "main")
    src = ast.get_source_segment(SRC, fn)
    assert "create_background_task(ticker_self_restart(app))" in src


def test_entrypoint_reexecs_when_restart_requested():
    guard = next(n for n in TREE.body if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Compare) and "__main__" in ast.unparse(n.test))
    src = ast.unparse(guard)
    assert "os.execv(sys.executable" in src and "_RESTART['want']" in src
    # the exec must come AFTER asyncio.run returns (terminal restored), i.e.
    # outside the try that wraps main(), not inside the ticker
    ticker = next(n for n in ast.walk(TREE) if isinstance(n, ast.AsyncFunctionDef) and n.name == "ticker_self_restart")
    assert "execv" not in ast.unparse(ticker) and "app.exit()" in ast.unparse(ticker)
