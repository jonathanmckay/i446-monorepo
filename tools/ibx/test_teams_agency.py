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


def test_reply_does_not_eagerly_mark_read():
    """
    Feature: Lazy mark-as-read. reply() must NOT call _mark_chat_read
    (expensive Chrome tab). Mark-as-read is handled lazily by fetch_teams_items
    when it detects stubborn unreads still in search results.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "reply":
            func_source = ast.get_source_segment(source, node)
            assert "_mark_chat_read" not in func_source, (
                "reply() must NOT call _mark_chat_read — mark-as-read is lazy"
            )
            return
    raise AssertionError("reply function not found")


def test_archive_does_not_eagerly_mark_read():
    """
    Feature: Lazy mark-as-read. archive() must NOT call _mark_chat_read
    (expensive Chrome tab). Mark-as-read is handled lazily by fetch_teams_items
    when it detects stubborn unreads still in search results.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "archive":
            func_source = ast.get_source_segment(source, node)
            assert "_mark_chat_read" not in func_source, (
                "archive() must NOT call _mark_chat_read — mark-as-read is lazy"
            )
            return
    raise AssertionError("archive function not found")


def test_mark_chat_read_exists():
    """_mark_chat_read function must exist and open Teams web in Chrome."""
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_mark_chat_read":
            func_source = ast.get_source_segment(source, node)
            assert "teams.microsoft.com" in func_source, (
                "_mark_chat_read must open Teams web URL"
            )
            return
    raise AssertionError("_mark_chat_read function not found")


def test_mark_chat_read_uses_chrome_msft_profile():
    """
    Fix: Open the chat URL in Chrome's MSFT profile (Profile 1), which has
    Teams auth cookies. Teams web marks the chat as read and syncs to desktop.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_mark_chat_read":
            func_source = ast.get_source_segment(source, node)
            assert "Google Chrome" in func_source, (
                "_mark_chat_read must open URL in Google Chrome"
            )
            assert "profile-directory" in func_source, (
                "_mark_chat_read must specify Chrome profile directory"
            )
            return
    raise AssertionError("_mark_chat_read function not found")


def test_close_only_ibx_tabs():
    """
    Bug: _close_teams_tabs closed ALL teams.microsoft.com tabs in Chrome,
    including tabs the user had open for their own work.

    Fix: close_ibx_teams_tabs only closes tabs containing the ibx0mark
    fingerprint parameter, leaving user's own Teams tabs untouched.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "close_ibx_teams_tabs":
            func_source = ast.get_source_segment(source, node)
            assert "ibx0mark" in func_source or "_IBX_TAB_MARKER" in func_source, (
                "close_ibx_teams_tabs must filter by ibx0mark fingerprint"
            )
            return
    raise AssertionError("close_ibx_teams_tabs function not found")


def test_mark_chat_read_includes_tab_marker():
    """
    Bug: _mark_chat_read opened plain teams.microsoft.com/l/chat/ URLs
    with no way to distinguish them from user's own Teams tabs.

    Fix: URL must include ibx0mark parameter so close_ibx_teams_tabs
    can identify and close only ibx0-opened tabs.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_mark_chat_read":
            func_source = ast.get_source_segment(source, node)
            assert "_IBX_TAB_MARKER" in func_source, (
                "_mark_chat_read URL must include _IBX_TAB_MARKER fingerprint"
            )
            return
    raise AssertionError("_mark_chat_read function not found")


def test_fetch_retries_mark_read_for_processed_items():
    """
    Bug: Messages archived via ibx0 were marked processed locally, but
    _mark_chat_read failed silently in a daemon thread. On next fetch,
    ibx0 skipped them (already processed) so they stayed unread in Teams
    forever — 'ghost unreads' visible in Teams but invisible in ibx0.

    Fix: fetch_teams_items must retry _mark_chat_read for processed items
    that still appear in search results (once per chat_id per fetch cycle).
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_teams_items":
            func_source = ast.get_source_segment(source, node)
            assert "_mark_chat_read" in func_source, (
                "fetch_teams_items must call _mark_chat_read for processed items "
                "still appearing in search results"
            )
            assert "_retry_read_chats" in func_source, (
                "fetch_teams_items must track retried chat_ids to avoid "
                "redundant mark-as-read calls within a single fetch cycle"
            )
            return
    raise AssertionError("fetch_teams_items function not found")


def test_mark_chat_read_validates_membership():
    """
    Bug: _mark_chat_read opened a Chrome tab for every chat ID without
    checking if the user is still a member. For chats the user had left,
    Teams web showed "We can't take you to that message because it's in
    a chat you're not in."

    Fix: _mark_chat_read must call GetChat to verify membership before
    opening a Chrome tab. If GetChat fails (returns None), skip silently.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_mark_chat_read":
            func_source = ast.get_source_segment(source, node)
            assert "GetChat" in func_source, (
                "_mark_chat_read must call GetChat to verify user is in the chat "
                "before opening a Chrome tab"
            )
            return
    raise AssertionError("_mark_chat_read function not found")
