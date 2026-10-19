"""Regression (2026-10-18): "-1n habits for last cycle didn't count".

Excel on ix hung at ~09:25 (main thread stuck inside an AppleEvent handler,
no workbook open, 0% CPU). Every Neon write after that timed out, so the
10/12/14/16/18 block fires all logged a correct `reconcile_p ... total=N`
and then `reconcile_p: FAILED osascript_timeout` — 0分!P never moved. The
daily com.jm.neon-open launchd job that should have reopened Excel invoked
`build-order-daemon.py ensure-open`, a CLI mode dropped in the 2026-07-28
excel-http refactor, so it had been failing "invalid choice" every morning.

Fix: `ensure_excel_ready()` probes Excel with a short AppleEvent timeout and
recovers all three dead states (not running / workbook closed / hung →
kill+relaunch); it is wired into `run_lock_and_mark` before the first cell
write and restored as the `ensure-open` CLI mode.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DAEMON = HERE / "build-order-daemon.py"
SRC = DAEMON.read_text()


def _func_src(name: str) -> str:
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"{name} not found in build-order-daemon.py")


def test_cli_accepts_ensure_open_mode():
    """com.jm.neon-open.plist runs `build-order-daemon.py ensure-open` daily;
    argparse must accept it or the agent fails before doing anything."""
    body = _func_src("main")
    assert '"ensure-open"' in body, "ensure-open missing from argparse choices"
    assert 'args.mode == "ensure-open"' in body


def test_lock_and_mark_gates_on_excel_before_first_write():
    body = _func_src("run_lock_and_mark")
    gate = body.index("ensure_excel_ready(")
    first_write = body.index("neon_lock_cell_with_retry(")
    assert gate < first_write, (
        "ensure_excel_ready must run before the first Excel write of the fire"
    )


def _load_daemon(monkeypatch):
    """Import the daemon with its lib deps stubbed so no ssh/Excel is touched."""
    lib = HERE.parent / "lib"
    monkeypatch.syspath_prepend(str(lib))
    excel_stub = types.SimpleNamespace(IS_IX=True)
    neon_pkg = types.ModuleType("neon")
    neon_pkg.excel = excel_stub
    monkeypatch.setitem(sys.modules, "neon", neon_pkg)
    monkeypatch.setitem(sys.modules, "neon.excel", excel_stub)
    spec = importlib.util.spec_from_file_location("bod_ensure_test", DAEMON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _P:
    def __init__(self, out="", rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


def test_hung_excel_is_killed_and_relaunched_with_workbook(monkeypatch):
    mod = _load_daemon(monkeypatch)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(mod.time, "time", iter(range(0, 10_000, 1)).__next__)
    calls: list[list[str]] = []
    alive = {"excel": True}
    probes = {"n": 0}

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["pgrep", "-x"]:
            return _P("87076\n" if alive["excel"] else "")
        if cmd[0] == "pkill":
            alive["excel"] = False
            return _P()
        if cmd[0] == "open":
            alive["excel"] = True
            return _P()
        if cmd[0] == "osascript":
            probes["n"] += 1
            if probes["n"] == 1:          # first probe: the wedged instance
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
            return _P(mod.NEON_XLSX.name + "\n")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.ensure_excel_ready() == "relaunched"
    kinds = [c[0] for c in calls]
    assert "pkill" in kinds, "a hung Excel must be terminated"
    assert kinds.index("pkill") < kinds.index("open"), "kill before relaunch"
    open_cmd = next(c for c in calls if c[0] == "open")
    assert open_cmd[-1] == str(mod.NEON_XLSX), "relaunch must open the Neon workbook"


def test_healthy_excel_is_left_alone(monkeypatch):
    mod = _load_daemon(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["pgrep", "-x"]:
            return _P("87076\n")
        if cmd[0] == "osascript":
            return _P(mod.NEON_XLSX.name + "\n")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.ensure_excel_ready() == "ok"
    assert not any(c[0] in ("pkill", "open") for c in calls)


def test_hung_excel_not_killed_when_disabled(monkeypatch):
    mod = _load_daemon(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["pgrep", "-x"]:
            return _P("87076\n")
        if cmd[0] == "osascript":
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.ensure_excel_ready(kill_if_hung=False) == "hung"
    assert not any(c[0] == "pkill" for c in calls)


def test_never_acts_off_ix(monkeypatch):
    mod = _load_daemon(monkeypatch)
    mod.neon_excel.IS_IX = False
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: pytest.fail("must not touch Excel off ix"))
    assert mod.ensure_excel_ready() == "not_ix"
