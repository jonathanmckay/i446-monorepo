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


def test_slack_mpim_with_missing_unread_count_still_shows():
    """Behavioral test: an MPIM where conversations.info returns NO unread_count
    field but there ARE messages newer than last_read should still appear.
    """
    from unittest.mock import patch, MagicMock
    import slack as _slack

    token = "xoxp-fake"
    self_id = "U_SELF"
    channel = {"id": "G_MPIM", "is_im": False, "is_mpim": True, "user": ""}

    # conversations.info returns last_read but NO unread_count (real MPIM behavior)
    info_response = {
        "ok": True,
        "channel": {
            "id": "G_MPIM",
            "last_read": "1000000000.000000",  # old
            # NOTE: no "unread_count" key at all — this is the bug trigger
        }
    }

    # conversations.history returns a message NEWER than last_read
    history_response = {
        "ok": True,
        "messages": [
            {"type": "message", "user": "U_OTHER", "text": "hey!", "ts": "1000000999.000000"},
        ]
    }

    members_response = {
        "ok": True,
        "members": ["U_SELF", "U_OTHER"]
    }

    def fake_slack_get(tok, method, **kwargs):
        if method == "conversations.info":
            return info_response
        elif method == "conversations.history":
            return history_response
        elif method == "conversations.members":
            return members_response
        elif method == "users.info":
            return {"ok": True, "user": {"real_name": "Other User", "profile": {"display_name": "other"}}}
        return {}

    with patch.object(_slack, "slack_get", side_effect=fake_slack_get):
        result = _slack.build_thread(token, channel, self_id)

    # Before the fix: result would be None (unread_count defaulted to 0 → filtered out)
    # After the fix: result is not None because latest msg ts > last_read
    assert result is not None, (
        "MPIM with missing unread_count but newer messages must NOT be filtered out"
    )
    assert result["messages"][0]["text"] == "hey!"


def test_slack_read_channel_is_filtered():
    """Counterpart: a channel where last_read >= latest message SHOULD be filtered."""
    from unittest.mock import patch
    import slack as _slack

    token = "xoxp-fake"
    self_id = "U_SELF"
    channel = {"id": "D_IM", "is_im": True, "user": "U_OTHER"}

    info_response = {
        "ok": True,
        "channel": {"id": "D_IM", "last_read": "2000000000.000000"}  # newer than messages
    }
    history_response = {
        "ok": True,
        "messages": [
            {"type": "message", "user": "U_OTHER", "text": "old msg", "ts": "1000000000.000000"},
        ]
    }

    def fake_slack_get(tok, method, **kwargs):
        if method == "conversations.info":
            return info_response
        elif method == "conversations.history":
            return history_response
        elif method == "users.info":
            return {"ok": True, "user": {"real_name": "Other", "profile": {"display_name": "other"}}}
        return {}

    with patch.object(_slack, "slack_get", side_effect=fake_slack_get):
        result = _slack.build_thread(token, channel, self_id)

    assert result is None, "Channel where last_read > latest msg should be filtered out"


def test_autosign_checks_html_body_for_appfolio_url():
    """Bug: forwarded countersign emails had the AppFolio URL only in the HTML body,
    not in the text/plain part. is_autosign_email returned False because
    extract_appfolio_url only searched the plaintext body.
    Fix: is_autosign_email and _autosign_item now also check html_body.
    """
    source = open("../m5x2-automations/lease_signer.py").read()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "is_autosign_email":
            body_src = ast.get_source_segment(source, node)
            assert "html_body" in body_src, (
                "is_autosign_email must check html_body for AppFolio URLs "
                "(forwarded emails often only have links in HTML)"
            )
            break
    else:
        raise AssertionError("is_autosign_email() not found")

    # ibx.get_email must include html_body field
    ibx_source = open("ibx.py").read()
    assert "html_body" in ibx_source, "get_email must return html_body field"


def test_prompt_uses_readline_escapes_not_console_print():
    """Bug: typing a long reply at the '> ' prompt that visually wrapped to a
    second terminal line made backspace stop working at the wrap boundary.
    Cause: console.print(ANSI, end="") before input("> ") meant readline only
    knew the prompt was 2 chars wide, so it miscalculated the wrap column.
    Fix: merge the counter prefix into the input() prompt using \\001/\\002
    readline escape markers around ANSI codes so readline tracks the full
    visible width correctly.
    """
    for filename in ("ibx.py", "imsg.py", "slack.py"):
        source = open(filename).read()
        tree = ast.parse(source)

        # The old pattern: console.print(..., end="") immediately before input("> ")
        # must not appear anywhere — the prompt should be passed directly to input()
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if 'end=""' in line and "console.print" in line and "[dim][" in line:
                # Next non-blank line should NOT be input("> ")
                for j in range(i + 1, min(i + 5, len(lines))):
                    stripped = lines[j].strip()
                    if not stripped or stripped.startswith("#") or stripped.startswith("try"):
                        continue
                    assert 'input("> ")' not in stripped, (
                        f"{filename}:{j + 1}: console.print(ANSI, end='') before "
                        f"input('> ') breaks readline line-wrap/backspace — "
                        f"merge prefix into input() prompt with \\001/\\002 escapes"
                    )
                    break

        # Positive check: input() prompt must contain \x01 / \x02 readline markers
        assert "\\001" in source or "\x01" in source, (
            f"{filename}: input prompt must use \\001/\\002 readline escape markers "
            f"around ANSI codes so readline calculates visible width correctly"
        )


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
