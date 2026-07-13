"""Regression tests for the tg-tui SIGUSR1 pid-file registration.

Bug (2026-06-11): a task started in dtd didn't show in tg-tui for ~60s and the
idle alarm kept flashing. toggl_cli sends SIGUSR1 to the pid in
~/.cache/tg-tui.pid after every mutating command, but the file was gone: any
second tg-tui instance's exit cleanup unconditionally unlinked it, deleting the
LIVE instance's registration. With no registration, every timer change degrades
to the 30s poll.

Fix: ownership-guarded release (_release_pid_file only unlinks its own pid) and
self-healing registration (_assert_pid_file re-asserts on every 30s tick).
"""
import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_pid", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_pid"] = mod
    spec.loader.exec_module(mod)
    return mod


def _with_pid_file(tmp_path, monkeypatch):
    mod = _load_tui()
    monkeypatch.setattr(mod, "PID_FILE", tmp_path / "tg-tui.pid")
    return mod


def test_assert_pid_file_writes_when_missing(tmp_path, monkeypatch):
    mod = _with_pid_file(tmp_path, monkeypatch)
    mod._assert_pid_file()
    assert mod.PID_FILE.read_text().strip() == str(os.getpid())


def test_assert_pid_file_reclaims_foreign_pid(tmp_path, monkeypatch):
    """Self-heal: a stale/foreign registration gets overwritten on the tick."""
    mod = _with_pid_file(tmp_path, monkeypatch)
    mod.PID_FILE.write_text("99999999")
    mod._assert_pid_file()
    assert mod.PID_FILE.read_text().strip() == str(os.getpid())


def test_release_does_not_unlink_foreign_pid(tmp_path, monkeypatch):
    """THE regression: a second instance exiting must not delete the live
    instance's registration."""
    mod = _with_pid_file(tmp_path, monkeypatch)
    mod.PID_FILE.write_text("99999999")  # owned by the other (live) instance
    mod._release_pid_file()
    assert mod.PID_FILE.exists(), "exit cleanup deleted another instance's pid file"
    assert mod.PID_FILE.read_text().strip() == "99999999"


def test_release_unlinks_own_pid(tmp_path, monkeypatch):
    mod = _with_pid_file(tmp_path, monkeypatch)
    mod.PID_FILE.write_text(str(os.getpid()))
    mod._release_pid_file()
    assert not mod.PID_FILE.exists()


def test_ticker_current_reasserts_pid_file():
    """Structural: the 30s current tick must self-heal the registration."""
    src = (HERE / "tg-tui.py").read_text()
    ticker = src.split("async def ticker_current", 1)[1].split("async def", 1)[0]
    assert "_assert_pid_file()" in ticker, \
        "ticker_current no longer re-asserts the pid file"
