"""Regression tests for ibx slack integration."""
import ast
import inspect
from pathlib import Path

SLACK_PY = Path(__file__).parent / "slack.py"
IBX_ALL_PY = Path(__file__).parent / "ibx_all.py"

REQUIRED_SCOPES = {
    "channels:history", "channels:read", "channels:write",
    "groups:history", "groups:read", "groups:write",
    "im:history", "im:read", "im:write",
    "mpim:history", "mpim:read", "mpim:write",
    "chat:write", "users:read",
}


def test_setup_instructions_list_all_required_scopes():
    """conversations.mark needs *:write scopes — ensure setup instructions include them."""
    source = SLACK_PY.read_text()
    for scope in REQUIRED_SCOPES:
        assert scope in source, f"Missing scope '{scope}' in slack.py setup instructions"


def test_fetch_recent_channels_does_not_filter_on_unread_count():
    """Slack free plan doesn't return unread_count — ensure we don't gate on it."""
    source = SLACK_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_recent_channels":
            func_source = ast.get_source_segment(source, node)
            assert "unread_count" not in func_source, (
                "fetch_recent_channels should not filter on unread_count"
            )
            return
    raise AssertionError("fetch_recent_channels function not found")


def test_ibx_all_slack_status_does_not_say_unread():
    """Status line should say 'recent' not 'unread' since we fetch all recent channels."""
    source = IBX_ALL_PY.read_text()
    assert '": {count} unread"' not in source, (
        "ibx_all.py Slack status line should not say 'unread' — we fetch recent, not unread"
    )


def test_build_thread_checks_read_state():
    """build_thread should check conversations.info for unread_count before building."""
    source = SLACK_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_thread":
            func_source = ast.get_source_segment(source, node)
            assert "conversations.info" in func_source, (
                "build_thread must call conversations.info to check read state"
            )
            assert "unread_count" in func_source, (
                "build_thread must check unread_count from conversations.info"
            )
            return
    raise AssertionError("build_thread function not found")
