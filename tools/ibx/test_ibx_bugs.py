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


def test_slack_thread_filtered_when_user_already_replied():
    """Bug: Slack messages the user already replied to kept appearing as new
    inbox cards. build_thread checked last_read but not whether the user's
    own reply was the latest message. If the user replied but Slack hadn't
    updated last_read yet, the thread re-appeared.
    Fix: compare latest outbound ts against latest inbound ts. If the user's
    latest message is newer than the latest inbound, skip the thread."""
    from unittest.mock import patch
    import slack as _slack

    token = "xoxp-fake"
    self_id = "U_SELF"
    channel = {"id": "D_IM", "is_im": True, "user": "U_OTHER"}

    # last_read is OLDER than both messages (Slack hasn't caught up)
    info_response = {
        "ok": True,
        "channel": {"id": "D_IM", "last_read": "1000000000.000000"}
    }
    # User replied AFTER the inbound message (newest first)
    history_response = {
        "ok": True,
        "messages": [
            {"type": "message", "user": "U_SELF", "text": "check with Ian first!", "ts": "1000000200.000000"},
            {"type": "message", "user": "U_OTHER", "text": "Name rings a bell", "ts": "1000000100.000000"},
        ]
    }

    def fake_slack_get(tok, method, **kwargs):
        if method == "conversations.info":
            return info_response
        elif method == "conversations.history":
            return history_response
        elif method == "users.info":
            return {"ok": True, "user": {"real_name": "Lawrence", "profile": {"display_name": "lawrence"}}}
        return {}

    with patch.object(_slack, "slack_get", side_effect=fake_slack_get):
        result = _slack.build_thread(token, channel, self_id)

    assert result is None, (
        "Thread where user's latest reply is newer than latest inbound "
        "message must be filtered out"
    )


def test_slack_thread_shown_when_new_inbound_after_reply():
    """Counterpart: if someone replies AFTER the user, the thread should show."""
    from unittest.mock import patch
    import slack as _slack

    token = "xoxp-fake"
    self_id = "U_SELF"
    channel = {"id": "D_IM", "is_im": True, "user": "U_OTHER"}

    info_response = {
        "ok": True,
        "channel": {"id": "D_IM", "last_read": "1000000000.000000"}
    }
    # Other person replied AFTER the user (newest first)
    history_response = {
        "ok": True,
        "messages": [
            {"type": "message", "user": "U_OTHER", "text": "actually wait", "ts": "1000000300.000000"},
            {"type": "message", "user": "U_SELF", "text": "done", "ts": "1000000200.000000"},
            {"type": "message", "user": "U_OTHER", "text": "hey", "ts": "1000000100.000000"},
        ]
    }

    def fake_slack_get(tok, method, **kwargs):
        if method == "conversations.info":
            return info_response
        elif method == "conversations.history":
            return history_response
        elif method == "users.info":
            return {"ok": True, "user": {"real_name": "Lawrence", "profile": {"display_name": "lawrence"}}}
        return {}

    with patch.object(_slack, "slack_get", side_effect=fake_slack_get):
        result = _slack.build_thread(token, channel, self_id)

    assert result is not None, (
        "Thread with new inbound message after user's reply must still show"
    )


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


def test_ibx0_continuous_polling_and_immediate_blue():
    """Bug: ibx0 only polled for new items when the card stack emptied,
    then did a blocking re-fetch before turning the terminal blue.
    Fix: continuous background fetch thread polls all sources on a timer.
    When cards reach zero, terminal goes blue immediately — no blocking fetch.
    """
    source = open("ibx0.py").read()
    tree = ast.parse(source)

    # 1. _bg_continuous_fetch function must exist
    bg_fetch_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_bg_continuous_fetch":
            body_src = ast.get_source_segment(source, node)
            bg_fetch_found = True
            # Must use ThreadPoolExecutor for parallel fetching
            assert "ThreadPoolExecutor" in body_src, (
                "_bg_continuous_fetch must fetch sources in parallel"
            )
            # Must wait for drainer to finish before starting
            assert "wait()" in body_src, (
                "_bg_continuous_fetch must wait for initial fetch to complete"
            )
            break
    assert bg_fetch_found, "_bg_continuous_fetch() not found in ibx0.py"

    # 2. main() must start the continuous fetch thread
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            body_src = ast.get_source_segment(source, node)
            assert "_bg_continuous_fetch" in body_src, (
                "main() must start the continuous fetch thread"
            )
            break

    # 3. The old blocking final-fetch pattern must NOT exist
    # (ThreadPoolExecutor in the stack-empty branch with _final_fetch)
    assert "_final_fetch" not in source, (
        "Blocking _final_fetch at stack-empty must be removed — "
        "terminal should go blue immediately, not after a re-fetch"
    )

    # 4. set_term_color("blue") must appear BEFORE any re-fetch in the
    #    stack-empty branch — verify by checking the inbox-zero wait loop
    #    uses select.select (non-blocking wait) not a blocking fetch
    assert "select.select" in source, (
        "Inbox-zero wait must use select.select for non-blocking user input"
    )


def test_ibx0_bg_fetch_suppresses_console_output():
    """Bug: background continuous fetch called fetch functions that print
    status messages (e.g. 'Teams — querying teams API...') to stdout,
    polluting the interactive card UI. Cards and prompts were scrolled
    off screen by background output, making ibx0 appear stuck.
    Fix: _bg_continuous_fetch swaps each source module's console to a
    quiet StringIO-backed Console during background fetches.
    """
    source = open("ibx0.py").read()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_bg_continuous_fetch":
            body_src = ast.get_source_segment(source, node)

            # Must swap module consoles to suppress output
            assert "mod.console = quiet" in body_src or "console = quiet" in body_src, (
                "_bg_continuous_fetch must suppress module console output during bg fetch"
            )

            # Must NOT swap ibx0's own console — that races with the main
            # thread's interactive prompts ("Send? (y/n)") causing hangs
            assert "global console" not in body_src, (
                "_bg_continuous_fetch must NOT swap ibx0's own console — "
                "it races with main-thread interactive prompts and causes hangs"
            )

            # Must restore original consoles afterward
            assert "mod.console = orig" in body_src or "console = orig" in body_src, (
                "_bg_continuous_fetch must restore original consoles after bg fetch"
            )

            # Must use finally block to guarantee restoration
            assert "finally" in body_src, (
                "_bg_continuous_fetch must restore consoles in a finally block"
            )
            break
    else:
        raise AssertionError("_bg_continuous_fetch() not found in ibx0.py")


def test_slack_get_retries_on_429():
    """Bug: continuous background polling hammered Slack API, triggering
    HTTP 429 (Too Many Requests). slack_get had no retry logic, so every
    429 surfaced as 'channel error' and the channel was skipped.
    Fix: slack_get retries up to 4 times with Retry-After backoff on 429.
    """
    source = open("slack.py").read()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "slack_get":
            body_src = ast.get_source_segment(source, node)

            # Must handle 429 status code
            assert "429" in body_src, (
                "slack_get must check for HTTP 429 and retry"
            )

            # Must use Retry-After header
            assert "Retry-After" in body_src, (
                "slack_get must respect the Retry-After header from 429 responses"
            )

            # Must have a retry loop
            assert "for attempt" in body_src or "retry" in body_src.lower(), (
                "slack_get must retry on 429, not fail immediately"
            )

            # Must sleep/backoff
            assert "time.sleep" in body_src, (
                "slack_get must sleep between retries"
            )
            break
    else:
        raise AssertionError("slack_get() not found in slack.py")


def test_bg_continuous_fetch_interval_not_too_aggressive():
    """The continuous fetch interval must be >= 120s to avoid hammering
    APIs (especially Slack which rate-limits at ~1 req/sec per method).
    """
    source = open("ibx0.py").read()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_bg_continuous_fetch":
            # Check the default interval parameter
            for arg, default in zip(node.args.args, node.args.defaults):
                pass  # walk to get defaults aligned
            # defaults align to the LAST N args
            args = node.args.args
            defaults = node.args.defaults
            offset = len(args) - len(defaults)
            for i, d in enumerate(defaults):
                arg_name = args[offset + i].arg
                if arg_name == "interval":
                    assert isinstance(d, ast.Constant), "interval default must be a constant"
                    assert d.value >= 120, (
                        f"_bg_continuous_fetch interval must be >= 120s to avoid rate limits, "
                        f"got {d.value}s"
                    )
                    break
            else:
                raise AssertionError("interval parameter not found in _bg_continuous_fetch")
            break
    else:
        raise AssertionError("_bg_continuous_fetch() not found")


def test_imsg_fetch_unread_skips_already_replied_threads():
    """Bug: threads where the user already replied directly in iMessage
    still appeared in ibx0 for review. The user saw their own replies
    in the conversation preview but was asked to review/respond again.
    Cause: fetch_unread_threads only looked at inbound messages (is_from_me=0)
    to determine latest_date. It didn't check if the user had sent a reply
    after the last inbound message.
    Fix: query also fetches latest_sent_date (is_from_me=1). If the user's
    latest outgoing message is newer than the latest inbound, skip the thread.
    """
    source = open("imsg.py").read()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_unread_threads":
            body_src = ast.get_source_segment(source, node)

            # SQL must fetch latest sent date alongside latest inbound date
            assert "latest_sent_date" in body_src, (
                "fetch_unread_threads must query latest_sent_date (is_from_me=1) "
                "to detect threads the user already replied to"
            )

            # Must compare sent vs received timestamps to skip replied threads
            assert "latest_sent_date" in body_src and "latest_date" in body_src, (
                "fetch_unread_threads must compare latest_sent_date > latest_date "
                "to skip threads where user already responded"
            )

            # SQL must not restrict WHERE to only is_from_me=0
            # (needs both inbound and outbound to compare)
            assert "WHERE m.is_from_me = 0" not in body_src, (
                "fetch_unread_threads SQL must not filter to only is_from_me=0 — "
                "it needs outbound messages to detect user replies"
            )
            break
    else:
        raise AssertionError("fetch_unread_threads() not found in imsg.py")


def test_body_truncation_keeps_newest_lines():
    """Bug: card body truncation kept the FIRST 20 lines, discarding the newest
    messages at the bottom of threads. Users need to see the latest messages.
    Fix: truncate from the top (keep last 20 lines) with '…' prefix."""
    source = open("ibx0.py").read()
    tree = ast.parse(source)

    # Find the body truncation block: look for the assignment that builds
    # the truncated wrapped list
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        # Look for: if len(wrapped) > 20
        test = node.test
        if not (isinstance(test, ast.Compare)
                and len(test.comparators) == 1):
            continue
        segment = ast.get_source_segment(source, node)
        if segment and "wrapped" in segment and "> 20" in segment:
            found = True
            # Must keep tail (wrapped[-20:]) not head (wrapped[:20])
            assert "wrapped[-20:]" in segment, (
                "Body truncation must keep the LAST 20 lines (wrapped[-20:]), "
                "not the first 20 (wrapped[:20])"
            )
            break
    assert found, "Body truncation if-block not found in ibx0.py"


def test_open_uses_chrome_profiles():
    """Bug: 'o' command opened URLs in default browser without the correct
    Chrome profile context (m5x2, MSFT, 个).
    Fix: do_open dispatches to _open_in_chrome with profile-specific directory."""
    source = open("ibx0.py").read()

    # _CHROME_PROFILES must map all three contexts
    assert '"m5x2"' in source and "Profile 11" in source, "m5x2 profile mapping missing"
    assert '"msft"' in source and "Profile 1" in source, "MSFT profile mapping missing"
    assert '"jbm"' in source and "Profile 5" in source, "jbm/个 profile mapping missing"

    # do_open must call _open_in_chrome, not bare subprocess open
    func_start = source.index("def do_open(")
    func_end = source.index("\ndef ", func_start + 1)
    func_src = source[func_start:func_end]
    assert "_open_in_chrome(" in func_src, "do_open must use _open_in_chrome"
    assert 'subprocess.run(["open", url])' not in func_src, \
        "do_open must not use bare open for URLs"


def test_expand_command_exists():
    """Bug: no way to see full message body when truncated.
    Fix: 'e' command shows the full body stored in item['_full_body']."""
    source = open("ibx0.py").read()
    tree = ast.parse(source)

    # The main loop should have an 'elif cmd == "e"' branch
    found_expand = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            segment = ast.get_source_segment(source, node)
            if segment and 'cmd == "e"' in segment:
                found_expand = True
                break
    assert found_expand, 'ibx0.py must have an elif cmd == "e" branch for expand'

    # _full_body must be stored during card rendering
    assert "_full_body" in source, "Card renderer must store _full_body on item for expand"


def test_processed_items_added_to_resolved():
    """Bug: items processed by the user (archive, delete, reply) were not added
    to the resolved set. When background continuous fetch re-fetched them and
    all_items was replaced (idle loop line: all_items = fresh), the old items
    reappeared as "new" because they weren't in resolved.
    Fix: mark all items before the current index as resolved at each loop iteration."""
    source = open("ibx0.py").read()

    # The main loop must add processed items to resolved
    # Find the section between "item = all_items[index]" and "display_card"
    # which should contain the resolved.add loop
    main_loop_section = source[source.index("item = all_items[index]") - 200:
                               source.index("item = all_items[index]") + 50]
    assert "resolved.add" in main_loop_section, (
        "Items before current index must be added to resolved set "
        "so background fetch doesn't re-inject them"
    )


def test_outlook_open_always_opens_browser():
    """Bug: hitting 'o' on an Outlook email did nothing in Chrome.
    Root cause: do_open() checked `if url:` but link was always empty
    because workiq prompt didn't ask for LINK. When empty, it silently
    returned without opening anything.
    Fix: fall back to Outlook web search URL when no link available.
    """
    source = open("ibx0.py").read()
    tree = ast.parse(source)

    # do_open must always call _open_in_chrome for outlook items,
    # not guard it behind `if url:`
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "do_open":
            body_src = ast.get_source_segment(source, node)
            # The outlook branch must have a fallback URL
            assert "outlook.office.com" in body_src, (
                "do_open() must fall back to Outlook web URL when link is empty"
            )
            break
    else:
        raise AssertionError("do_open function not found")


def test_workiq_prompt_asks_for_link():
    """Bug: workiq prompt didn't ask for LINK field, so Outlook emails
    never had a link to open.
    Fix: added LINK: to the workiq prompt.
    """
    source = open("outlook_workiq.py").read()
    assert "LINK:" in source, "workiq prompt must ask for LINK field"


def test_archive_log_db_tracks_actions():
    """Feature: archive actions are logged to a local SQLite database so the
    idle screen can show an accurate 'archived this block' count that survives
    process restarts and doesn't depend on flaky APIs."""
    source = open("ibx0.py").read()

    # DB helpers must exist
    assert "def _get_archive_db(" in source, "must have _get_archive_db function"
    assert "def _log_archive(" in source, "must have _log_archive function"
    assert "def _block_archive_count(" in source, "must have _block_archive_count function"
    assert "archive_log.db" in source, "must use archive_log.db file"

    # _log_archive must be called in do_archive, do_delete, do_reply
    for func_name in ("do_archive", "do_delete", "do_reply"):
        func_start = source.index(f"def {func_name}(")
        func_end = source.index("\ndef ", func_start + 1)
        func_src = source[func_start:func_end]
        assert "_log_archive(" in func_src, (
            f"{func_name} must call _log_archive after successful action"
        )

    # _block_archive_count must use COUNT(DISTINCT item_uid) to dedup
    count_start = source.index("def _block_archive_count(")
    count_end = source.index("\ndef ", count_start + 1)
    count_src = source[count_start:count_end]
    assert "DISTINCT item_uid" in count_src, (
        "_block_archive_count must use COUNT(DISTINCT item_uid) to avoid "
        "double-counting when do_reply also calls do_archive"
    )

    # _render_block_status must show archive count
    render_start = source.index("def _render_block_status(")
    render_end = source.index("\ndef ", render_start + 1)
    render_src = source[render_start:render_end]
    assert "_block_archive_count()" in render_src, (
        "idle screen must display archive count"
    )


def test_background_fetch_skips_triage():
    """Bug: background continuous fetch called fetch_emails() which ran
    triage_inbox(), removing INBOX labels from emails already in the user's
    queue. On the next loop iteration, _poll_resolved / filter_resolved saw
    those emails as "resolved elsewhere" and removed them — clearing the
    queue while the user was still reviewing it.
    Fix: fetch_emails() takes skip_triage param; background fetch passes True.
    """
    source = open("ibx0.py").read()
    tree = ast.parse(source)

    # 1. fetch_emails must accept skip_triage parameter
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_emails":
            arg_names = [a.arg for a in node.args.args]
            assert "skip_triage" in arg_names, (
                "fetch_emails() must accept skip_triage parameter"
            )
            break
    else:
        raise AssertionError("fetch_emails function not found")

    # 2. _bg_continuous_fetch must call fetch_emails with skip_triage=True
    bg_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_bg_continuous_fetch":
            bg_func = ast.get_source_segment(source, node)
            break
    assert bg_func is not None, "_bg_continuous_fetch not found"
    assert "skip_triage" in bg_func, (
        "_bg_continuous_fetch must pass skip_triage=True to fetch_emails"
    )


def test_reply_and_archive_counts_use_archive_log_db():
    """All reply/archive stats must come from archive_log.db (single source
    of truth). _block_reply_count, _block_archive_count, and _day_avg_response
    must all query the SQLite DB, not the old _response_times JSON."""
    source = open("ibx0.py").read()

    # All three stat functions must query archive_log
    for func_name in ("_block_reply_count", "_block_archive_count", "_day_avg_response"):
        func_start = source.index(f"def {func_name}(")
        func_end = source.index("\ndef ", func_start + 1)
        func_src = source[func_start:func_end]
        assert "_get_archive_db()" in func_src, (
            f"{func_name} must query archive_log.db via _get_archive_db()"
        )

    # _log_archive must store response_min for reply actions
    log_start = source.index("def _log_archive(")
    log_end = source.index("\ndef ", log_start + 1)
    log_src = source[log_start:log_end]
    assert "response_min" in log_src, (
        "_log_archive must compute and store response_min for replies"
    )

    # Old JSON system should be removed
    assert "_RESPONSE_TIMES_FILE" not in source, (
        "Old _RESPONSE_TIMES_FILE JSON system should be removed"
    )
    assert "def _load_response_times" not in source, (
        "Old _load_response_times should be removed"
    )
