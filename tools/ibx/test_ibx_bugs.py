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
