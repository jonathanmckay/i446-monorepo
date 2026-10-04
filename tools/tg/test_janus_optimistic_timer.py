#!/usr/bin/env python3
"""Regression (2026-10-04): Janus repainted the timer row only after the whole
job finished -- did-fast (4-11s) or tg-fast, then up to four SERIAL Toggl
re-reads -- so stop/done/switch looked frozen ("make Janus fast like dtd").

Fix: handlers set the expected timer row immediately (_optimistic_stop /
_optimistic_start); fetch_current keeps that view while Toggl still reports
the pre-action state, and lets reality win once it differs or after
OPTIMISTIC_HOLD_S. Post-command re-reads run concurrently, and tg-fast
confirmation is 1-2 read pairs instead of 3 live /current reads + 1 entries.
"""
import ast
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "janus.py").read_text()


def _load():
    spec = importlib.util.spec_from_file_location("janus_opt_test", HERE / "janus.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


J = _load()


class _API:
    def __init__(self, cur):
        self.cur = cur
    def get_current(self):
        return self.cur
    def get_current_cached(self, *a, **k):
        return self.cur


def _setup(monkeypatch, toggl_cur, ui_cur):
    monkeypatch.setattr(J, "toggl_api", _API(toggl_cur))
    monkeypatch.setattr(J, "_toggl_blocked", lambda: False)
    J.STATE.current = ui_cur
    J.STATE.optimistic = None


def test_stop_holds_until_toggl_reflects_it(monkeypatch):
    old = {"id": 11, "description": "deep work", "start": "2026-10-04T09:00:00-07:00"}
    _setup(monkeypatch, old, old)
    J._optimistic_stop()
    assert J.STATE.current is None
    J.fetch_current(cached=True)          # stale cache still shows the old timer
    assert J.STATE.current is None, "a stale read must not resurrect the stopped timer"
    J.toggl_api.cur = None                # Toggl catches up
    J.fetch_current()
    assert J.STATE.current is None and J.STATE.optimistic is None


def test_switch_shows_new_desc_then_adopts_real_entry(monkeypatch):
    old = {"id": 11, "description": "deep work"}
    _setup(monkeypatch, old, old)
    J._optimistic_start("email")
    assert J.STATE.current["description"] == "email" and J.STATE.current.get("_optimistic")
    J.fetch_current()
    assert J.STATE.current["description"] == "email", "old timer read must not overwrite the optimistic one"
    real = {"id": 22, "description": "email"}
    J.toggl_api.cur = real
    J.fetch_current()
    assert J.STATE.current is real and J.STATE.optimistic is None


def test_start_from_no_timer_holds_against_empty_reads(monkeypatch):
    _setup(monkeypatch, None, None)
    J._optimistic_start("meeting")
    J.fetch_current()
    assert J.STATE.current and J.STATE.current["description"] == "meeting"


def test_hold_expires(monkeypatch):
    old = {"id": 11, "description": "deep work"}
    _setup(monkeypatch, old, old)
    J._optimistic_stop()
    J.STATE.optimistic["until"] = time.monotonic() - 1
    J.fetch_current()
    assert J.STATE.current is old, "after the hold, Toggl's state wins even if unchanged (failed command)"


def _func_src(name):
    node = next(n for n in ast.walk(ast.parse(SRC)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(SRC, node)


def test_handlers_go_optimistic_before_the_job():
    done = _func_src("_run_done_command")
    assert done.index("_optimistic_stop()") < done.index("_enqueue_work(")
    assert "_refresh_parallel(" in done
    assert "polls = (0.4, 0.8, 1.5)" not in SRC, "the 4-read confirmation loop must be gone everywhere"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
