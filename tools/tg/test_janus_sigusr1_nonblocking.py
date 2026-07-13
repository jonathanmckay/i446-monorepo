"""Regression (2026-06-11): starting a task froze tg-tui in the inverted
(highlighted) idle-nag frame. _sigusr1_refresh ran fetch_points (Excel over
ssh, 4-15s) and the Toggl fetches synchronously on the event loop, blocking
all repaints; the last painted frame was the flash 'on' phase.

Structural guard: inside _sigusr1_refresh, no fetch_* may be a bare inline
call — each must be wrapped in asyncio.to_thread(...) or _bg_fetch(...).
"""
import ast
from pathlib import Path

HERE = Path(__file__).parent
FETCHES = {"fetch_current", "fetch_today", "fetch_points", "fetch_short_names"}


def _sigusr1_func() -> ast.AsyncFunctionDef:
    tree = ast.parse((HERE / "tg-tui.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_sigusr1_refresh":
            return node
    raise AssertionError("_sigusr1_refresh not found")


def test_sigusr1_refresh_never_fetches_on_the_event_loop():
    func = _sigusr1_func()
    offloaded_args = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        # asyncio.to_thread(fetch_x) / _bg_fetch(app, fetch_x): collect the
        # fetch functions passed as arguments — those are off-loop, fine.
        name = (callee.attr if isinstance(callee, ast.Attribute)
                else callee.id if isinstance(callee, ast.Name) else "")
        if name in ("to_thread", "_bg_fetch"):
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    offloaded_args.add(arg.id)
            continue
        # Any direct call to a fetch_* blocks the loop — the regression.
        assert name not in FETCHES, \
            f"{name}() called inline in _sigusr1_refresh — blocks the UI loop"

    missing = FETCHES - offloaded_args
    assert not missing, f"fetches no longer refreshed on SIGUSR1: {missing}"


def test_sigusr1_repaints_before_slow_fetches():
    """fetch_current must be awaited and followed by an invalidate() before
    fetch_today/fetch_points run, so the idle-nag invert clears immediately."""
    func = _sigusr1_func()
    order = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            callee = node.func
            name = (callee.attr if isinstance(callee, ast.Attribute)
                    else callee.id if isinstance(callee, ast.Name) else "")
            order.append((node.lineno, name))
        elif isinstance(node, ast.Name):
            order.append((node.lineno, node.id))
    order.sort()
    names = [n for _, n in order]
    i_current = names.index("fetch_current")
    i_today = names.index("fetch_today")
    assert "invalidate" in names[i_current:i_today], \
        "no repaint between fetch_current and the slower fetches"
