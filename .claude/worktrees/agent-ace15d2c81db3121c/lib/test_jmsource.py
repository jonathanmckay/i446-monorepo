"""Tests for lib/jmsource.py and the first-party write hooks that feed it.

JM Dash slices tasks and time by source (cli / 3p / ...). Toggl and Todoist
don't report which client wrote something, so lib/todoist.close_task and
toggl_api's create paths log their own writes. These tests pin: rows land in
a per-host monthly file, recording never raises, and both hooks fire only
after a successful API call.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "mcp"))

import jmsource  # noqa: E402


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    d = tmp_path / "source-log"
    monkeypatch.setattr(jmsource, "LOG_DIR", d)
    monkeypatch.setattr(jmsource, "_host", lambda: "testhost")
    monkeypatch.delenv("JMSOURCE", raising=False)
    monkeypatch.setenv("JMSOURCE_VIA", "pytest")
    return d


def test_record_appends_row_to_host_month_file(logdir):
    jmsource.record("task", 123)
    files = list(logdir.glob("testhost-*.jsonl"))
    assert len(files) == 1
    row = json.loads(files[0].read_text())
    assert row["metric"] == "task"
    assert row["ext_id"] == "123"
    assert row["source"] == "cli"
    assert row["via"] == "pytest"
    assert row["ts"].endswith("+00:00")
    assert row["local"][-6] in "+-"  # local time carries its offset
    assert "ok" not in row


def test_record_never_raises(logdir, monkeypatch):
    monkeypatch.setattr(jmsource.os, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    jmsource.record("task", 1)  # must not raise


def test_unknown_source_env_falls_back_to_cli(logdir, monkeypatch):
    monkeypatch.setenv("JMSOURCE", "bogus")
    jmsource.record("time", 9)
    assert jmsource.load()[0]["source"] == "cli"


def test_pytest_never_writes_real_log(monkeypatch, tmp_path):
    monkeypatch.setattr(jmsource, "LOG_DIR", jmsource._DEFAULT_LOG_DIR)
    monkeypatch.setattr(jmsource.os, "open", lambda *a, **k: pytest.fail("wrote real log"))
    jmsource.record("task", 1)


def test_load_skips_torn_lines(logdir):
    jmsource.record("task", 1)
    path = next(logdir.glob("*.jsonl"))
    with open(path, "a") as f:
        f.write('{"metric": "ta')
    assert [r["ext_id"] for r in jmsource.load()] == ["1"]


def _load_todoist(monkeypatch):
    spec = importlib.util.spec_from_file_location("todoist_t", HERE / "todoist.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_todoist_close_records_after_success(logdir, monkeypatch):
    td = _load_todoist(monkeypatch)
    monkeypatch.setattr(td, "_request", lambda *a, **k: None)
    td.close_task("abc")
    assert [(r["metric"], r["ext_id"]) for r in jmsource.load()] == [("task", "abc")]


def test_todoist_close_failure_records_nothing(logdir, monkeypatch):
    td = _load_todoist(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("Todoist POST → 500")
    monkeypatch.setattr(td, "_request", boom)
    with pytest.raises(RuntimeError):
        td.close_task("abc")
    assert jmsource.load() == []


def test_toggl_create_and_start_record_entry_ids(logdir, monkeypatch):
    from toggl_server import toggl_api
    ids = iter([111, 222])
    monkeypatch.setattr(toggl_api, "_request", lambda *a, **k: {"id": next(ids)})
    toggl_api.create_entry("x", "2026-10-05T10:00:00Z", "2026-10-05T10:30:00Z", 1800)
    toggl_api.start_timer("y")
    assert [(r["metric"], r["ext_id"]) for r in jmsource.load()] == [("time", "111"), ("time", "222")]
