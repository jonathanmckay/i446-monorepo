"""Regression tests for teams_agency."""
import ast
import textwrap
from pathlib import Path

TEAMS_AGENCY_PY = Path(__file__).parent / "teams_agency.py"
IBX0_PY = Path(__file__).parent / "ibx0.py"


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


# ── Thread grouping tests ─────────────────────────────────────────────────────

IBX0_PY = Path(__file__).parent / "ibx0.py"


def test_fetch_groups_by_chat_id():
    """
    Feature: Multiple unread messages in the same Teams chat thread must be
    grouped into a single card. fetch_teams_items must group hits by chat_id.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_teams_items":
            func_source = ast.get_source_segment(source, node)
            assert "chat_msgs" in func_source, (
                "fetch_teams_items must collect messages by chat_id for grouping"
            )
            assert "all_item_ids" in func_source, (
                "Grouped items must include all_item_ids in _data"
            )
            return
    raise AssertionError("fetch_teams_items function not found")


def test_grouped_item_has_all_item_ids():
    """
    Feature: Grouped Teams card must include all individual message IDs
    in _data['all_item_ids'] so archive/delete can mark all processed.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_teams_items":
            func_source = ast.get_source_segment(source, node)
            assert "all_item_ids" in func_source, (
                "Grouped items must store all_item_ids list"
            )
            assert "msg_count" in func_source, (
                "Grouped items must include msg_count in _data"
            )
            return
    raise AssertionError("fetch_teams_items function not found")


def test_archive_all_exists():
    """
    Feature: archive_all must mark all individual message IDs as processed
    but only record response-time action for the representative message.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "archive_all":
            func_source = ast.get_source_segment(source, node)
            assert "_mark_processed" in func_source, (
                "archive_all must call _mark_processed for each item"
            )
            assert "record_action" in func_source, (
                "archive_all must call record_action for the representative message"
            )
            found = True
            break
    assert found, "archive_all function not found"


def test_delete_all_exists():
    """
    Feature: delete_all must exist and delegate to archive_all.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "delete_all":
            func_source = ast.get_source_segment(source, node)
            assert "archive_all" in func_source, (
                "delete_all must delegate to archive_all"
            )
            return
    raise AssertionError("delete_all function not found")


def test_ibx0_item_uid_uses_chat_id():
    """
    Feature: _item_uid for Teams items must use chat_id (thread-level identity),
    not msg_id. This prevents duplicate cards when new messages arrive in the
    same thread between fetches.
    """
    source = IBX0_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_item_uid":
            func_source = ast.get_source_segment(source, node)
            assert "teams_thread" in func_source, (
                "_item_uid must return ('teams_thread', chat_id) for Teams items"
            )
            assert "chat_id" in func_source, (
                "_item_uid must use chat_id for Teams dedup"
            )
            return
    raise AssertionError("_item_uid function not found")


def test_ibx0_do_archive_handles_grouped():
    """
    Feature: do_archive must handle grouped Teams items by calling
    archive_all with all_item_ids.
    """
    source = IBX0_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "do_archive":
            func_source = ast.get_source_segment(source, node)
            assert "all_item_ids" in func_source, (
                "do_archive must check for all_item_ids in grouped Teams items"
            )
            assert "archive_all" in func_source, (
                "do_archive must call archive_all for grouped items"
            )
            return
    raise AssertionError("do_archive function not found")


def test_ibx0_do_reply_marks_all_grouped():
    """
    Feature: do_reply for Teams must mark all grouped message IDs as processed,
    not just the representative.
    """
    source = IBX0_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "do_reply":
            func_source = ast.get_source_segment(source, node)
            assert "all_item_ids" in func_source, (
                "do_reply must handle all_item_ids for grouped Teams items"
            )
            return
    raise AssertionError("do_reply function not found")


def test_grouped_body_contains_all_messages():
    """
    Feature: Grouped card body must concatenate all messages chronologically.
    """
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_teams_items":
            func_source = ast.get_source_segment(source, node)
            assert "lines" in func_source, (
                "Grouped items must concatenate all messages into body"
            )
            assert "sort" in func_source, (
                "Messages within a group must be sorted chronologically"
            )
            return
    raise AssertionError("fetch_teams_items function not found")


def test_ibx0_display_card_shows_msg_count():
    """
    Feature: display_card must show the message count for grouped Teams cards.
    """
    source = IBX0_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "display_card":
            func_source = ast.get_source_segment(source, node)
            assert "msg_count" in func_source, (
                "display_card must show msg_count for grouped Teams items"
            )
            return
    raise AssertionError("display_card function not found")


def test_teams_reply_raises_on_failure():
    """Regression: Teams reply silently succeeded even when PostMessage failed.
    do_reply didn't propagate the failure, so the UI showed 'Sent + done'
    while no message was actually sent.

    Fix: do_reply raises RuntimeError when _teams.reply returns False,
    which is caught by the try/except in the r command path."""
    source = IBX0_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "do_reply":
            func_source = ast.get_source_segment(source, node)
            assert "raise" in func_source and "teams" in func_source.lower(), (
                "do_reply must raise an exception when Teams reply fails "
                "so the UI doesn't falsely report success"
            )
            return
    raise AssertionError("do_reply function not found")


def test_teams_reply_rejects_empty_chat_id():
    """Regression: reply() with empty chat_id skipped the API call entirely
    but still returned True and recorded the action as successful.

    Fix: reply() must return False when chat_id is empty."""
    source = TEAMS_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "reply":
            func_source = ast.get_source_segment(source, node)
            assert "not chat_id" in func_source or "no chat_id" in func_source, (
                "reply() must explicitly reject empty chat_id"
            )
            assert "return False" in func_source, (
                "reply() must return False when chat_id is missing"
            )
            return
    raise AssertionError("reply function not found")


def test_teams_reply_uses_correct_tool_name():
    """Bug: reply() called _teams_call('PostMessage', ...) but the Agency MCP
    server's actual tool is 'SendMessageToChat'. PostMessage doesn't exist,
    so _teams_call caught the error and returned None, making every reply fail.
    Fix: use 'SendMessageToChat' as the tool name."""
    source = TEAMS_AGENCY_PY.read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "reply":
            func_source = ast.get_source_segment(source, node)
            assert "SendMessageToChat" in func_source, (
                "reply() must call 'SendMessageToChat', not 'PostMessage'"
            )
            assert "PostMessage" not in func_source, (
                "reply() must not reference the non-existent 'PostMessage' tool"
            )
            return
    raise AssertionError("reply function not found")
