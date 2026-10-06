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
