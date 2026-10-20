"""`/d <task>` in janus (user request 2026-10-06): "use the /d marker on janus
so it means to add a task to the todo list, and note that I'm working on it
now" — create a due-today Todoist task AND start a Toggl timer for it."""
import ast
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_d_cmd", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_d_cmd"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_parse_d_command_splits_todo_content_from_timer():
    mod = _load()
    assert mod.parse_d_command("/d write data leads JD (60) [20] @i9") == (
        "write data leads JD (60) [20] @i9", "write data leads JD @i9")
    assert mod.parse_d_command("/d plane, then bags {10}") == (
        "plane, then bags {10}", "plane, then bags")


def test_parse_d_command_accepts_trailing_marker_without_leaking_it_into_timer():
    # Bug 2026-10-19: "QBR in person [180] /d" started a Toggl timer named
    # "QBR in person [180] /d" instead of creating the task.
    mod = _load()
    assert mod.parse_d_command("QBR in person [180] /d") == (
        "QBR in person [180]", "QBR in person")


def test_parse_d_command_ignores_non_d_input():
    mod = _load()
    for text in ("/done", "/d", "/d   ", "/d [20]", "/d @i9", "d foo", "read /d foo", "/did x"):
        assert mod.parse_d_command(text) is None, text


def test_handler_routes_d_before_comma_split_and_refuses_past_days():
    src = (HERE / "janus.py").read_text()
    i_d = src.index("d_cmd = parse_d_command(text)")
    i_split = src.index('parts = [p.strip() for p in text.split(",") if p.strip()] or [text]')
    assert i_d < i_split, "/d must win before commas split the task name"
    block = src[i_d:i_split]
    assert "if STATE.day_offset:" in block
    assert "create_todo_task" in block and "run_tg_fast" in block
    ast.parse(src)


def test_d_task_writes_dtd_running_hint_and_stop_clears_it(tmp_path):
    mod = _load()
    mod.DTD_RUNNING_HINT = tmp_path / "dtd-running-hint"
    mod.write_dtd_running_hint({"id": "6hX", "content": "Write JD (60) [20]"})
    name, started, tid = mod.DTD_RUNNING_HINT.read_text().split("\t")
    assert (name, tid) == ("write jd", "6hX") and started.isdigit()
    mod._optimistic_stop()
    assert not mod.DTD_RUNNING_HINT.exists()
    src = (HERE / "janus.py").read_text()
    assert "write_dtd_running_hint(task)" in src[src.index("def create_todo_task"):]


def test_d_task_records_started_today_so_dtd_shows_abacus(tmp_path, monkeypatch):
    # Bug 2026-10-19: janus /d started the timer and wrote the running hint,
    # but never wrote dtd-started, which is what dtd's 🧮 abacus reads. Once
    # the timer moved on, the task lost its abacus.
    mod = _load()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    mod.record_dtd_started("6hX")
    line = (tmp_path / "jm" / "dtd-started").read_text().strip().split("\t")
    assert line[0] == mod.dt.date.today().isoformat() and line[1] == "6hX" and line[2].isdigit()
    src = (HERE / "janus.py").read_text()
    body = src[src.index("def create_todo_task"):]
    body = body[:body.index("\ndef ", 1)]
    assert "record_dtd_started(task.get" in body, "create_todo_task must record the started entry"
