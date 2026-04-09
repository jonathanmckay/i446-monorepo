"""Regression tests for teams_workiq."""
import ast
from pathlib import Path

TEAMS_PY = Path(__file__).parent / "teams_workiq.py"


def test_teams_prompt_no_unread_request():
    """
    Bug: Asking workiq for 'unread' Teams messages causes refusal because
    workiq cannot determine read/unread status. The prompt must ask for
    'recent' messages instead.
    """
    source = TEAMS_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_teams_items":
            func_source = ast.get_source_segment(source, node)
            # Find the _run_workiq call and check its string argument
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    # Check if this is _run_workiq(...)
                    func_name = ""
                    if isinstance(child.func, ast.Name):
                        func_name = child.func.id
                    elif isinstance(child.func, ast.Attribute):
                        func_name = child.func.attr
                    if func_name == "_run_workiq" and child.args:
                        prompt_node = child.args[0]
                        prompt_src = ast.get_source_segment(source, prompt_node)
                        assert "unread" not in prompt_src.lower(), (
                            "Teams prompt must not request 'unread' messages — "
                            "workiq cannot determine read/unread status and will refuse"
                        )
                        return
    raise AssertionError("_run_workiq call not found in fetch_teams_items")


def test_teams_filters_group_chats():
    """Teams fetch must filter out group chat messages (only show 1:1 DMs)."""
    source = TEAMS_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_teams_items":
            func_source = ast.get_source_segment(source, node)
            assert "group" in func_source.lower(), (
                "fetch_teams_items must filter group chat messages"
            )
            return
    raise AssertionError("fetch_teams_items function not found")


def test_teams_skips_empty_messages():
    """Teams items with no message text should be filtered out, not shown as '(no message text available)'."""
    source = TEAMS_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_teams_items":
            func_source = ast.get_source_segment(source, node)
            assert "not message" in func_source or "if not message" in func_source, (
                "fetch_teams_items must skip items with empty message text"
            )
            return
    raise AssertionError("fetch_teams_items function not found")


def test_teams_archive_no_workiq_call():
    """
    Bug: archive() called _run_workiq() to try marking chats as read, which takes
    60-120 seconds and freezes the UI. workiq can't mark chats as read anyway.

    Fix: archive() must NOT call _run_workiq. It should only do local bookkeeping.
    """
    source = TEAMS_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "archive":
            func_source = ast.get_source_segment(source, node)
            assert "_run_workiq" not in func_source, (
                "archive() must not call _run_workiq — it freezes the UI for 60-120s "
                "and workiq cannot mark Teams chats as read anyway"
            )
            return
    raise AssertionError("archive function not found")


def test_workiq_timeout_no_raw_error():
    """
    Bug: workiq timeout printed raw subprocess error with full command path,
    e.g. "Command '['/Users/...workiq', 'ask', '-q', '...']' timed out after 120 seconds"

    Fix: catch TimeoutExpired separately and print a clean message.
    """
    source = TEAMS_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_workiq":
            func_source = ast.get_source_segment(source, node)
            # Must NOT have a generic f-string error with {e} for timeout
            assert "workiq error: {e}" not in func_source, (
                "_run_workiq must not print raw exception for timeouts — "
                "use a clean message like 'workiq timed out'"
            )
            # Must handle TimeoutExpired specifically
            assert "TimeoutExpired" in func_source, (
                "_run_workiq must catch TimeoutExpired explicitly"
            )
            return
    raise AssertionError("_run_workiq function not found")
