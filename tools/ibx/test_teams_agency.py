"""Regression tests for teams_agency."""
import ast
from pathlib import Path

TEAMS_AGENCY_PY = Path(__file__).parent / "teams_agency.py"


def test_module_imports_on_python39():
    """
    Bug: `_graph_identity: dict | None = None` uses PEP 604 union syntax
    which requires Python 3.10+. On Python 3.9 the module fails to import
    with TypeError, so ibx0 silently sets _teams_available=False and all
    Teams messages are invisible.

    Fix: use plain assignment without type annotation, or use Optional[dict].
    """
    source = TEAMS_AGENCY_PY.read_text()
    # Must not use PEP 604 union syntax (X | Y) in runtime annotations
    assert "dict | None" not in source and "None | dict" not in source, (
        "Module must not use 'dict | None' syntax — breaks Python 3.9. "
        "Use Optional[dict] or a comment annotation instead."
    )
    # Verify the module actually compiles
    compile(source, str(TEAMS_AGENCY_PY), "exec")


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


def test_reply_marks_chat_read():
    """
    Bug: replying to a Teams thread via PostMessage did not mark the chat
    as read in the Teams app. Threads showed as unread even after responding.

    Fix: reply() and archive() must call _mark_chat_read(chat_id) to invoke
    the Graph API markChatReadForUser endpoint.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "reply":
            func_source = ast.get_source_segment(source, node)
            assert "_mark_chat_read" in func_source, (
                "reply() must call _mark_chat_read to mark the chat as read in Teams"
            )
            return
    raise AssertionError("reply function not found")


def test_archive_marks_chat_read():
    """
    Bug: archiving a Teams message only updated local processed.json.
    The chat stayed unread in the Teams app.

    Fix: archive() must accept chat_id and call _mark_chat_read().
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "archive":
            func_source = ast.get_source_segment(source, node)
            assert "_mark_chat_read" in func_source, (
                "archive() must call _mark_chat_read to mark the chat as read in Teams"
            )
            assert "chat_id" in func_source, (
                "archive() must accept chat_id parameter"
            )
            return
    raise AssertionError("archive function not found")


def test_mark_chat_read_exists():
    """_mark_chat_read function must exist and call markChatReadForUser."""
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_mark_chat_read":
            func_source = ast.get_source_segment(source, node)
            assert "markChatReadForUser" in func_source, (
                "_mark_chat_read must call the Graph API markChatReadForUser endpoint"
            )
            return
    raise AssertionError("_mark_chat_read function not found")
