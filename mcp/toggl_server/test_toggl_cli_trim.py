"""Regression (2026-11-02): /0g Step 0 trimmed the running timer with a
SKILL.md one-liner that imported toggl_api loose from the package dir; its
relative imports failed and no API key loaded. `toggl_cli.py trim N` does it
through the CLI's own key loader."""
import datetime
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("toggl_cli_trim", HERE / "toggl_cli.py")
cli = importlib.util.module_from_spec(_spec)
sys.modules["toggl_cli_trim"] = cli
_spec.loader.exec_module(cli)
UTC = datetime.timezone.utc


def _setup(monkeypatch, current):
    calls = []
    monkeypatch.setattr(cli.toggl_api, "get_current_cached", lambda: current)
    monkeypatch.setattr(cli.toggl_api, "get_current", lambda: current)
    monkeypatch.setattr(cli.toggl_api, "update_entry", lambda eid, **kw: calls.append((eid, kw)))
    return calls


def test_trim_sets_stop_n_minutes_ago(monkeypatch):
    start = datetime.datetime.now(UTC) - datetime.timedelta(minutes=30)
    calls = _setup(monkeypatch, {"id": 7, "start": start.isoformat()})
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_trim(["2"])
    (eid, kw), = calls
    stop = datetime.datetime.strptime(kw["stop"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert eid == 7
    assert abs((datetime.datetime.now(UTC) - stop).total_seconds() - 120) < 10
    assert "Trimmed 7" in buf.getvalue()


def test_trim_refuses_to_stop_before_start(monkeypatch):
    start = datetime.datetime.now(UTC) - datetime.timedelta(seconds=30)
    calls = _setup(monkeypatch, {"id": 8, "start": start.isoformat()})
    with pytest.raises(SystemExit):
        cli.cmd_trim(["2"])
    assert calls == []


def test_trim_no_timer(monkeypatch):
    calls = _setup(monkeypatch, None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_trim([])
    assert "No timer running" in buf.getvalue() and calls == []


def test_trim_is_registered_and_mutating():
    assert "trim" in cli.COMMANDS and "trim" in cli._MUTATING_COMMANDS
