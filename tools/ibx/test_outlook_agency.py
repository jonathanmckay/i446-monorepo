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


def test_fetch_uses_received_and_isread():
    """
    Bug: lastModifiedDateTime filter was too broad — pulled in emails that were
    modified for any reason (read, categorized, moved) even if they're not in
    the inbox. Showed 20+ phantom emails.

    Fix: Use receivedDateTime + isRead eq false. This matches emails that are
    genuinely new and unread. processed.json handles dedup.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            assert "receivedDateTime ge" in func_source, (
                "fetch_outlook_items must use receivedDateTime for time cutoff"
            )
            assert "isRead eq false" in func_source, (
                "fetch_outlook_items must filter on isRead eq false"
            )
            assert "lastModifiedDateTime ge" not in func_source, (
                "fetch_outlook_items must NOT use lastModifiedDateTime — "
                "it's too broad and pulls in read/moved/categorized emails"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")


def test_fetch_filters_noise_senders():
    """
    Bug: ibx0 showed automated notification emails (MSApprovals, MyExpense,
    SharePoint noreply, Benefits) that Outlook puts in the Other tab.
    User sees 0 in Focused inbox but 5+ in ibx0.

    Fix: filter known noise sender addresses.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            assert "NOISE_SENDERS" in func_source, (
                "fetch_outlook_items must filter NOISE_SENDERS"
            )
            assert "msaemail@microsoft.com" in func_source, (
                "NOISE_SENDERS must include MSApprovals (msaemail@microsoft.com)"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")
