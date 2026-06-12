"""Regression tests for outlook_agency."""
import ast
import sqlite3
from pathlib import Path

import outlook_agency

OUTLOOK_AGENCY_PY = Path(__file__).parent / "outlook_agency.py"


def test_record_action_uses_timezone_aware_now(tmp_path, monkeypatch):
    """
    Bug: record_action() called datetime.now(timezone.utc) without importing
    timezone, so reply/archive/delete paths crashed with NameError at runtime.

    Fix: import timezone at module scope and verify record_action updates the DB.
    """
    db_path = tmp_path / "response_times.db"
    monkeypatch.setattr(outlook_agency, "RESPONSE_DB", db_path)

    item_id = "outlook:test-message"
    outlook_agency.record_fetch(
        item_id,
        "Sender <sender@example.com>",
        "Subject",
        "2026-04-12T12:00:00Z",
    )

    outlook_agency.record_action(item_id, "reply")

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT action, action_at, response_hours FROM outlook_responses WHERE item_id = ?",
            (item_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "reply"
    assert row[1]


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


def test_archive_clears_message():
    """
    History: (1) archive() originally marked read via UpdateMessage, but the
    tool silently ignored isRead — emails stayed unread and kept reappearing.
    (2) Fix was delete-from-inbox, which destroyed mail (Deleted Items).
    (3) 2026-06-12 (JM): archive() must be non-destructive again — mark read
    via _clear_after_action, which VERIFIES the flag via GetMessage and only
    falls back to DeleteMessage if mark-read doesn't stick.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "archive":
            func_source = ast.get_source_segment(source, node)
            assert "_clear_after_action" in func_source, (
                "archive() must call _clear_after_action (mark-read, verified, "
                "delete fallback) to clear the email from the unread queue"
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


def test_noreply_not_blanket_blocked():
    """
    Bug: noreply@microsoft.com was in NOISE_SENDERS, which blanket-blocked
    ALL emails from that address. This filtered out wanted mail like
    'Reaction Daily Digest' along with actual noise (SharePoint notifications).

    Fix: remove noreply@microsoft.com from NOISE_SENDERS. Instead, use a
    subject-based filter (NOREPLY_NOISE_SUBJECT_RE) to skip only noise
    subjects from that address, letting legitimate mail through.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            # noreply@microsoft.com must NOT be in NOISE_SENDERS
            # Find the NOISE_SENDERS block specifically
            import re
            noise_block = re.search(
                r'NOISE_SENDERS\s*=\s*\{([^}]+)\}', func_source, re.DOTALL
            )
            assert noise_block, "NOISE_SENDERS set not found"
            assert "noreply@microsoft.com" not in noise_block.group(1), (
                "noreply@microsoft.com must NOT be in NOISE_SENDERS — "
                "it sends both noise and wanted mail (e.g. Reaction Daily Digest)"
            )
            # Must have subject-aware filtering for noreply
            assert "NOREPLY_NOISE_SUBJECT_RE" in func_source, (
                "Must use NOREPLY_NOISE_SUBJECT_RE to selectively filter "
                "noreply@microsoft.com by subject"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")

def test_reply_archives_email():
    """
    Bug: reply() sent the reply via Graph API but did not remove the email
    from the inbox. The email stayed visible even after responding.

    Fix: reply() must call _clear_after_action after a successful ReplyToMessage
    so the email leaves the unread queue reliably (mark-read, delete fallback).
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "reply":
            func_source = ast.get_source_segment(source, node)
            assert "_clear_after_action" in func_source, (
                "reply() must call _clear_after_action to clear the email after replying"
            )
            assert "Thread" in func_source, (
                "reply() must run _clear_after_action in a background thread "
                "to avoid blocking the UI after sending"
            )
            return
    raise AssertionError("reply function not found")


def test_reply_all_archives_email():
    """
    Bug: reply_all() sent the reply via Graph API but did not remove the
    email from the inbox (same issue as reply()).

    Fix: reply_all() must call _clear_after_action after successful ReplyAllToMessage.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "reply_all":
            func_source = ast.get_source_segment(source, node)
            assert "_clear_after_action" in func_source, (
                "reply_all() must call _clear_after_action to clear the email after replying"
            )
            return
    raise AssertionError("reply_all function not found")


def test_fetch_checks_legacy_workiq_ids():
    """
    Bug: Switching from outlook_workiq to outlook_agency changed item ID format
    from 'workiq:sender:subject' to 'outlook:graph_msg_id'. All previously
    processed emails resurfaced because the new IDs didn't match old ones.

    Fix: fetch_outlook_items must check both new and legacy ID formats against
    processed.json.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            assert "workiq:" in func_source, (
                "fetch_outlook_items must check legacy workiq: ID format "
                "for backwards compatibility with processed.json"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")


def test_clear_after_action_verifies_and_falls_back():
    """
    Contract for _clear_after_action (2026-06-12, JM):
    1. Try mark-read via UpdateMessage (non-destructive, message stays in inbox).
    2. VERIFY via GetMessage that isRead actually flipped — a past regression
       had UpdateMessage silently ignoring isRead (emails reappeared forever).
    3. Fall back to DeleteMessage (old behavior) with one retry if mark-read
       doesn't stick, and warn the user if everything fails.
    4. Runs synchronously (callers wrap it in a thread).
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_clear_after_action":
            func_source = ast.get_source_segment(source, node)
            assert "UpdateMessage" in func_source, (
                "_clear_after_action must try mark-read via UpdateMessage first"
            )
            assert "GetMessage" in func_source and "isRead" in func_source, (
                "_clear_after_action must VERIFY the isRead flag via GetMessage "
                "(UpdateMessage has silently ignored isRead before)"
            )
            assert "DeleteMessage" in func_source, (
                "_clear_after_action must fall back to DeleteMessage when "
                "mark-read does not stick"
            )
            assert "Thread" not in func_source, (
                "_clear_after_action must be synchronous — callers thread it"
            )
            assert "could not clear" in func_source.lower(), (
                "_clear_after_action must warn user if all attempts fail"
            )
            return
    raise AssertionError("_clear_after_action function not found")


def test_archive_uses_clear_after_action_in_thread():
    """
    Bug: a synchronous clear blocks the UI for up to ~45s (sleeps + multiple
    15s timeouts) on every archive action — the card freezes while Graph
    times out.

    Fix: archive() must call _clear_after_action in a daemon thread so the
    UI returns immediately. _clear_after_action retries internally.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "archive":
            func_source = ast.get_source_segment(source, node)
            assert "_clear_after_action" in func_source, (
                "archive() must use _clear_after_action to clear reliably"
            )
            assert "Thread" in func_source, (
                "archive() must run _clear_after_action in a background thread "
                "to avoid blocking the UI"
            )
            return
    raise AssertionError("archive function not found")


def test_fetch_retries_delete_for_processed_items():
    """
    Bug: Outlook emails replied to via ibx0 were marked processed locally,
    but _delete_after_action failed silently. On next fetch, ibx0 skipped
    them (already processed) so they stayed in Outlook inbox forever —
    'ghost unreads' visible in Outlook but invisible in ibx0.

    Fix: fetch_outlook_items must retry _clear_after_action for processed
    items still appearing in the unread query, with a persisted cleared set
    to avoid redundant calls.
    """
    source = OUTLOOK_AGENCY_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_outlook_items":
            func_source = ast.get_source_segment(source, node)
            assert "_clear_after_action" in func_source, (
                "fetch_outlook_items must call _clear_after_action for processed items "
                "still appearing in the unread query"
            )
            assert "already_deleted" in func_source, (
                "fetch_outlook_items must track cleared IDs to avoid "
                "redundant calls"
            )
            return
    raise AssertionError("fetch_outlook_items function not found")
