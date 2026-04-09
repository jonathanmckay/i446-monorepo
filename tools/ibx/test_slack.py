"""Regression tests for ibx slack integration."""
import ast
import inspect
from pathlib import Path

SLACK_PY = Path(__file__).parent / "slack.py"

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
