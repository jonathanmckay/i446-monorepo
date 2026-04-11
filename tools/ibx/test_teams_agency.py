"""Regression tests for teams_agency."""
import ast
from pathlib import Path

TEAMS_AGENCY_PY = Path(__file__).parent / "teams_agency.py"


def test_archive_not_daemon_thread():
    """
    Bug: archive() ran record_action + _mark_processed in a daemon thread.
    Daemon threads are killed when the main process exits (user presses 'q').
    processed.json was never written, so archived items reappeared on restart.

    Fix: archive() must run synchronously — these are instant local operations.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "archive":
            func_source = ast.get_source_segment(source, node)
            assert "daemon" not in func_source and "Thread" not in func_source, (
                "archive() must not use daemon threads for _mark_processed — "
                "daemon threads die on exit, losing the processed.json write"
            )
            return
    raise AssertionError("archive function not found")


def test_fetch_uses_graph_ids():
    """
    Teams items must use stable Graph API IDs (teams:{chatId}:{msgId}),
    not workiq-style sender:message text IDs.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_teams_items":
            func_source = ast.get_source_segment(source, node)
            assert "chat_id" in func_source and "msg_id" in func_source, (
                "Item IDs must use Graph API chatId + msgId for stability"
            )
            return
    raise AssertionError("fetch_teams_items function not found")
