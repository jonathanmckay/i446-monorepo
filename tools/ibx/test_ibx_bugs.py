"""Regression tests for ibx bugs."""
import ast
import re


def test_archive_removes_inbox_from_entire_thread():
    """Bug: archiving one message left other messages in the thread with INBOX label.
    Gmail's `in:inbox` is thread-level, so the archived thread kept reappearing.
    Fix: archive() now gets the threadId and removes INBOX from ALL messages in the thread.
    """
    source = open("ibx.py").read()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "archive":
            body_src = ast.get_source_segment(source, node)
            # Must reference threadId / threads().get to archive the whole thread
            assert "threadId" in body_src, "archive() must get the thread ID"
            assert "threads" in body_src, "archive() must fetch the thread to get all messages"
            break
    else:
        raise AssertionError("archive() function not found in ibx.py")


def test_normalize_email_skips_sent_by_user():
    """Bug: user's own sent replies appeared as inbox items because Gmail
    thread-level queries return the latest message (which could be sent).
    Fix: normalize_email returns None when from-address is in MY_EMAILS.
    """
    source = open("ibx0.py").read()

    # normalize_email must reference MY_EMAILS to filter sent messages
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "normalize_email":
            body_src = ast.get_source_segment(source, node)
            assert "MY_EMAILS" in body_src, "normalize_email must check MY_EMAILS"
            assert "return None" in body_src, "normalize_email must return None for sent messages"
            break
    else:
        raise AssertionError("normalize_email() not found in ibx0.py")

    # MY_EMAILS must contain the known addresses
    assert "mckay@m5x2.com" in source, "MY_EMAILS must include mckay@m5x2.com"
    assert "mckay@m5c7.com" in source, "MY_EMAILS must include mckay@m5c7.com"


def test_fetch_inbox_dedup_threads_shows_one_message_per_thread():
    """Bug: every message in a Gmail thread appeared as a separate review item.
    User should only see the most recent message per thread.
    Fix: fetch_inbox(dedup_threads=True) keeps only the first message per threadId.
    """
    from unittest.mock import MagicMock

    import ibx

    svc = MagicMock()
    svc.users().messages().list().execute.return_value = {
        "messages": [
            {"id": "m1", "threadId": "tA"},
            {"id": "m2", "threadId": "tA"},  # older msg in same thread
            {"id": "m3", "threadId": "tB"},
            {"id": "m4", "threadId": "tA"},  # yet another in same thread
            {"id": "m5", "threadId": "tB"},  # older msg in thread B
        ]
    }

    # Without dedup: all 5 messages returned
    result = ibx.fetch_inbox(svc, dedup_threads=False)
    assert len(result) == 5

    # With dedup: only first message per thread (m1 for tA, m3 for tB)
    result = ibx.fetch_inbox(svc, dedup_threads=True)
    assert len(result) == 2
    assert result[0]["id"] == "m1"
    assert result[1]["id"] == "m3"


def test_slack_build_thread_uses_message_ts_not_unread_count():
    """Bug: Slack unread DMs not showing in ibx because build_thread relied on
    conversations.info unread_count which is MISSING for MPIMs (group DMs).
    All MPIMs defaulted to unread_count=0 and were filtered out.
    Fix: compare last_read against the actual latest message timestamp instead.
    """
    source = open("slack.py").read()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_thread":
            body_src = ast.get_source_segment(source, node)
            # Must NOT rely on unread_count for filtering
            assert 'unread_count", 0)' not in body_src, (
                "build_thread must not use unread_count for read/unread filtering — "
                "it's missing for MPIMs"
            )
            # Must compare last_read against latest message ts
            assert "latest_msg_ts" in body_src or "latest" in body_src, (
                "build_thread must compare last_read against actual message timestamps"
            )
            break
    else:
        raise AssertionError("build_thread() not found in slack.py")


def test_ibx0_fetch_emails_uses_dedup_threads():
    """Verify ibx0.fetch_emails calls fetch_inbox with dedup_threads=True
    so users only review one message per thread.
    """
    source = open("ibx0.py").read()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_emails":
            body_src = ast.get_source_segment(source, node)
            assert "dedup_threads=True" in body_src, (
                "fetch_emails must call fetch_inbox with dedup_threads=True"
            )
            break
    else:
        raise AssertionError("fetch_emails() not found in ibx0.py")
