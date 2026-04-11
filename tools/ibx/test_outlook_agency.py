"""Regression tests for outlook_agency."""
import ast
from pathlib import Path

OUTLOOK_AGENCY_PY = Path(__file__).parent / "outlook_agency.py"


def test_fromisoformat_handles_z_suffix():
    """
    Bug: Python 3.9 datetime.fromisoformat() doesn't accept trailing 'Z'.
    Graph API returns dates like '2026-04-08T22:35:49Z' which crashes with
    'Invalid isoformat string'.

    Fix: .replace("Z", "+00:00") before parsing.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    # Every fromisoformat call must handle Z suffix
    import re
    calls = re.findall(r'fromisoformat\([^)]+\)', source)
    assert calls, "No fromisoformat calls found"
    for call in calls:
        assert 'replace' in call and 'Z' in call, (
            f"fromisoformat call must handle Z suffix: {call}"
        )


def test_archive_deletes_message():
    """
    Bug: archive() tried to mark as read via UpdateMessage, but that tool
    doesn't support isRead. Emails stayed unread in Graph and kept reappearing.

    Fix: archive() calls DeleteMessage to definitively remove from inbox.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "archive":
            func_source = ast.get_source_segment(source, node)
            assert "DeleteMessage" in func_source, (
                "archive() must call DeleteMessage to remove email from inbox"
            )
            return
    raise AssertionError("archive function not found")


def test_fetch_filters_calendar_responses():
    """
    Bug: ibx showed calendar responses (Accepted, Declined, Canceled) and
    the user's own sent emails as cards.

    Fix: filter calendar subject prefixes + skip own sent emails.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            assert "Accepted" in func_source and "Canceled" in func_source, (
                "fetch_outlook_items must filter calendar response subjects"
            )
            assert "MY_ADDRESSES" in func_source or "jomckay" in func_source, (
                "fetch_outlook_items must skip own sent emails"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")


def test_fetch_no_isread_filter():
    """
    Bug: Query used 'isRead eq false' but Outlook Focused Inbox read state
    doesn't match Graph API's isRead flag. Emails the user sees as unread
    in Outlook were isRead=True in Graph and got filtered out.

    Fix: Don't filter on isRead. Fetch all recent emails (last 24h) and let
    processed.json handle dedup.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            # The query string must NOT contain isRead filter
            assert "isRead eq false" not in func_source, (
                "fetch_outlook_items must not filter on isRead — "
                "Outlook Focused Inbox read state doesn't match Graph isRead"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")


def test_fetch_uses_lastmodified_not_received():
    """
    Bug: Snoozed emails have old receivedDateTime but get resurfaced to inbox
    when snooze expires. The 24h filter on receivedDateTime excluded them.

    Fix: Filter on lastModifiedDateTime — catches snoozed, flagged, and
    resurfaced emails.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            assert "lastModifiedDateTime" in func_source, (
                "fetch_outlook_items must filter on lastModifiedDateTime, not receivedDateTime — "
                "snoozed emails have old receivedDateTime but updated lastModifiedDateTime"
            )
            assert "receivedDateTime ge" not in func_source, (
                "fetch_outlook_items must not use receivedDateTime for the time cutoff"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")
