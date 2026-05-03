"""Regression tests for ibx0.py."""
import ast
import time
from datetime import datetime, timezone
from pathlib import Path

IBX_ALL_PY = Path(__file__).parent / "ibx0.py"


def test_outlook_wait_not_gated_on_empty_inbox():
    """
    Bug: ibx blocks on 'waiting for outlook, teams...' even when fast sources
    (Gmail/iMsg/Slack) already returned items to review. Should only block
    when there are NO items to show.

    Fix: gate the wait on 'not all_items' — only block when inbox is truly empty
    and slow sources haven't finished yet.
    """
    source = IBX_ALL_PY.read_text()
    lines = source.splitlines()

    # Find the slow-source wait block (the one with slow_pending)
    for i, line in enumerate(lines):
        if "slow_pending" in line and "waiting for" in lines[min(i + 1, len(lines) - 1)]:
            context = "\n".join(lines[max(0, i - 5):i + 1])
            assert "not all_items" in context, (
                "Slow-source wait must be gated on 'not all_items' — "
                "don't block when there are already items to review"
            )
            return
    raise AssertionError("slow-source wait block not found")


# ── Response time feature tests ──────────────────────────────────────────────

def test_parse_received_at_exists():
    """_parse_received_at must be defined and handle all item types."""
    tree = ast.parse(IBX_ALL_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_received_at":
            source = ast.get_source_segment(IBX_ALL_PY.read_text(), node)
            assert "email" in source, "_parse_received_at must handle email type"
            assert "slack" in source, "_parse_received_at must handle slack type"
            assert "imsg" in source, "_parse_received_at must handle imsg type"
            return
    raise AssertionError("_parse_received_at function not found")


def test_print_response_stats_exists():
    """_print_response_stats must be defined and track running average."""
    tree = ast.parse(IBX_ALL_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_print_response_stats":
            source = ast.get_source_segment(IBX_ALL_PY.read_text(), node)
            assert "_response_times" in source, \
                "_print_response_stats must use _response_times list"
            assert "append" in source, \
                "_print_response_stats must append to _response_times"
            return
    raise AssertionError("_print_response_stats function not found")


def test_response_stats_called_after_every_reply():
    """Every do_reply call site must be followed by _print_response_stats."""
    source = IBX_ALL_PY.read_text()
    lines = source.splitlines()

    reply_sites = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Find do_reply calls that are NOT the function definition
        if "do_reply(item," in stripped and not stripped.startswith("def "):
            reply_sites.append(i)

    assert len(reply_sites) >= 4, f"Expected >=4 do_reply call sites, found {len(reply_sites)}"

    for line_num in reply_sites:
        # Check that _print_response_stats appears within 3 lines after do_reply
        after = "\n".join(lines[line_num:line_num + 4])
        assert "_print_response_stats" in after, (
            f"do_reply at line {line_num + 1} is not followed by _print_response_stats. "
            "Every reply must show response time stats."
        )


def test_normalize_sets_received_at():
    """All normalize functions must set received_at on the returned item."""
    source = IBX_ALL_PY.read_text()

    for func_name in ("normalize_email", "normalize_imsg", "normalize_slack"):
        assert f'"received_at"' in source or "'received_at'" in source, \
            f"{func_name} must set received_at on normalized items"

    # Check that fetch_outlook and fetch_teams also set received_at
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("fetch_outlook", "fetch_teams"):
            func_source = ast.get_source_segment(source, node)
            assert "received_at" in func_source, \
                f"{node.name} must set received_at on fetched items"


def test_response_times_tracker_exists():
    """Module must have _response_times list for tracking."""
    source = IBX_ALL_PY.read_text()
    assert "_response_times" in source, "Module must define _response_times list"
    # Verify it's initialized as a list
    assert "_response_times:" in source or "_response_times =" in source, \
        "_response_times must be initialized"


def test_response_times_persisted_to_disk():
    """_response_times must be persisted to disk so day average survives restarts."""
    source = IBX_ALL_PY.read_text()
    # Must have load and save functions
    assert "_load_response_times" in source, \
        "Must have _load_response_times to restore day average across restarts"
    assert "_save_response_times" in source, \
        "Must have _save_response_times to persist day average across restarts"
    # _save must be called inside _print_response_stats
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_print_response_stats":
            func_source = ast.get_source_segment(source, node)
            assert "_save_response_times" in func_source, \
                "_print_response_stats must call _save_response_times after appending"
            return
    raise AssertionError("_print_response_stats function not found")


def test_pipe_through_tracks_response_stats():
    """Pipe-through reply commands (p/P) must call _print_response_stats."""
    source = IBX_ALL_PY.read_text()
    lines = source.splitlines()

    pipe_sites = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "pipe_through(" in stripped and not stripped.startswith("def "):
            pipe_sites.append(i)

    assert len(pipe_sites) >= 2, \
        f"Expected >=2 pipe_through call sites, found {len(pipe_sites)}"

    for line_num in pipe_sites:
        # Check that _print_response_stats appears within 6 lines after pipe_through
        after = "\n".join(lines[line_num:line_num + 7])
        assert "_print_response_stats" in after, (
            f"pipe_through at line {line_num + 1} is not followed by "
            "_print_response_stats. Pipe-through replies must track response time."
        )


# ── Single-line status feature tests ─────────────────────────────────────────

def test_fetch_functions_use_update_status():
    """fetch_emails/imsgs/slack must call _update_status, not console.print for progress."""
    source = IBX_ALL_PY.read_text()
    tree = ast.parse(source)

    for func_name in ("fetch_emails", "fetch_imsgs", "fetch_slack"):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                func_source = ast.get_source_segment(source, node)
                assert "_update_status" in func_source, \
                    f"{func_name} must use _update_status for progress reporting"
                break


def test_status_line_function_exists():
    """_status_line must exist and reference all source names."""
    source = IBX_ALL_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_status_line":
            func_source = ast.get_source_segment(source, node)
            assert "_fetch_status" in func_source, \
                "_status_line must read from _fetch_status dict"
            return
    raise AssertionError("_status_line function not found")


def test_live_display_used_in_main():
    """main() must start and stop a Live display for the status line."""
    source = IBX_ALL_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            func_source = ast.get_source_segment(source, node)
            assert "_live" in func_source, "main must use _live for status display"
            assert ".start()" in func_source, "main must start the Live display"
            assert ".stop()" in func_source, "main must stop the Live display"
            return
    raise AssertionError("main function not found")


def test_drainer_done_event_prevents_race():
    """
    Bug: after _fetch_done fires, the main thread used a 0.3s sleep hoping
    _bg_drainer would finish its final drain. If the drainer was slow, items
    sat in _bg_injected and the main thread declared 'Inbox zero' despite
    having items. Manifested as 'teams: 4 to review' followed by 'Inbox zero'.

    Fix: _bg_drainer must set a _drainer_done event after its final drain,
    and the main thread must wait on _drainer_done instead of sleeping.
    """
    source = IBX_ALL_PY.read_text()
    tree = ast.parse(source)

    # _bg_drainer must set _drainer_done
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_bg_drainer":
            func_source = ast.get_source_segment(source, node)
            assert "_drainer_done.set()" in func_source, (
                "_bg_drainer must signal _drainer_done after final drain"
            )
            break
    else:
        raise AssertionError("_bg_drainer function not found")

    # main() must wait on _drainer_done, not use time.sleep for drain sync
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            func_source = ast.get_source_segment(source, node)
            assert "_drainer_done.wait(" in func_source, (
                "main must wait on _drainer_done event instead of sleeping"
            )
            return
    raise AssertionError("main function not found")


def test_final_fetch_includes_teams():
    """
    Bug: the 'final parallel fetch before declaring inbox zero' only checked
    email, imsg, slack, and outlook — Teams was missing. If all items were
    Teams messages, the final sweep would miss them entirely.

    Fix: include fetch_teams in the final fetch list.
    """
    source = IBX_ALL_PY.read_text()
    # Find the final fetch block
    assert "fetch_teams" in source.split("One final parallel fetch")[1].split("Inbox zero")[0], (
        "Final parallel fetch before inbox zero must include fetch_teams"
    )


def test_wrapper_supports_force_refresh():
    """
    Bug: ibx0_wrapper.sh used 'sleep $POLL_INTERVAL' after inbox zero,
    which couldn't be interrupted. Users had to wait the full 60s or Ctrl+C.

    Fix: Use 'read -t' so any keypress forces an immediate refresh.
    """
    wrapper = Path(__file__).parent / "ibx0_wrapper.sh"
    text = wrapper.read_text()
    assert "read -t" in text, (
        "ibx0_wrapper.sh must use 'read -t' for interruptible wait, not 'sleep'"
    )
    assert "Enter to refresh" in text or "enter to refresh" in text.lower(), (
        "ibx0_wrapper.sh must tell the user they can press Enter to refresh"
    )


def test_display_card_truncates_long_lines():
    """
    Bug: When card body content had lines longer than terminal width, they
    wrapped into multiple terminal lines. This confused readline's cursor
    tracking at the input() prompt, making backspace/delete stop working.

    Fix: display_card must truncate individual lines to terminal width and
    cap total line count to prevent excessive wrapping.
    """
    source = IBX_ALL_PY.read_text()
    assert "get_terminal_size" in source or "term_width" in source, (
        "display_card must check terminal width to truncate long lines"
    )
    # Body section must limit lines
    assert "body_lines" in source or "splitlines" in source, (
        "display_card must split body into lines for truncation"
    )


def test_fast_source_wait_breaks_on_items():
    """
    Bug: The fast-source wait loop (up to 8s) only broke when ALL three fast
    sources (email, imsg, slack) had reported. If a slow source like Teams
    returned items first, the user still had to wait up to 8s before seeing
    any card.

    Fix: Also break out of the wait loop when _incoming queue has items,
    so the first card is shown as soon as ANY source returns results.
    """
    source = IBX_ALL_PY.read_text()
    lines = source.splitlines()

    for i, line in enumerate(lines):
        if "fast_deadline" in line and "time.time() + " in line:
            # Find the while loop that follows
            block = "\n".join(lines[i:i + 10])
            assert "_incoming.empty()" in block or "_incoming" in block, (
                "Fast-source wait loop must break when items are already "
                "queued in _incoming, not just when all fast sources finish"
            )
            return
    raise AssertionError("fast_deadline wait loop not found in main()")


def test_drainer_wait_is_nonblocking():
    """
    Bug: _drainer_done.wait(timeout=5) blocked for up to 5 additional seconds
    after the fast-source wait, even when items were already drained and ready.
    Combined with the 8s fast-source wait, users waited ~13s before seeing
    the first card.

    Fix: Use a near-zero timeout (≤0.5s). The main loop already handles
    late-arriving items via _bg_injected injection.
    """
    source = IBX_ALL_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            func_source = ast.get_source_segment(source, node)
            # Find _drainer_done.wait calls and check timeout values
            import re
            waits = re.findall(r'_drainer_done\.wait\(timeout=(\d+(?:\.\d+)?)\)', func_source)
            assert waits, "_drainer_done.wait() call not found in main()"
            for t in waits:
                assert float(t) <= 0.5, (
                    f"_drainer_done.wait timeout is {t}s — must be ≤0.5s "
                    "to avoid blocking card display. Late items are handled "
                    "by the main loop's _bg_injected injection."
                )
            return
    raise AssertionError("main() function not found")


def test_background_injection_after_input_not_before_card():
    """
    Bug: Background item arrivals and poll messages were printed at the TOP
    of the main loop, between card display iterations. This interrupted the
    card the user was reading — e.g. printing "+ 2 item(s) arrived in
    background" in the middle of reading a Teams DM.

    Fix: Move background injection and poll message printing to AFTER
    user_input = input("> "), so the card + prompt are shown uninterrupted.
    Background items are silently queued until the user acts.
    """
    source = IBX_ALL_PY.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            func_source = ast.get_source_segment(source, node)
            lines = func_source.splitlines()

            # Find the main card loop's input(">") and "arrived in background" print
            input_line = None
            arrived_line = None
            for i, line in enumerate(lines):
                if ('input("> ")' in line or 'input(\"> \")' in line) and input_line is None:
                    input_line = i
                if "arrived in background" in line:
                    arrived_line = i

            assert input_line is not None, "input prompt not found in main()"
            assert arrived_line is not None, "background arrival print not found in main()"

            assert arrived_line > input_line, (
                f"Background arrival message (line {arrived_line}) must come AFTER "
                f"input prompt (line {input_line}), not before card display. "
                "Printing during card view interrupts the user."
            )
            return
    raise AssertionError("main() function not found")


def test_response_stats_uses_dashboard_clamp():
    """Bug: /ibx '⏱ Response time' didn't match the project blocking dashboard.

    The dashboard clamps via comms_response_clamp.clamp_response_hours_unix
    (resets at PST midnight, caps at 24h). ibx0 used a raw `now - received`
    delta. _print_response_stats must call the same clamp so the numbers align.
    """
    source = IBX_ALL_PY.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_print_response_stats":
            func_source = ast.get_source_segment(source, node)
            assert "clamp_response_hours_unix" in func_source, (
                "_print_response_stats must call clamp_response_hours_unix "
                "from comms_response_clamp so /ibx numbers match the "
                "project blocking dashboard"
            )
            return
    raise AssertionError("_print_response_stats function not found")


def test_clamp_imported_from_dashboard():
    """ibx0 must import the clamp helper from personal-dashboard's
    comms_response_clamp module (single source of truth)."""
    source = IBX_ALL_PY.read_text()
    assert "comms_response_clamp" in source, (
        "ibx0 must import from comms_response_clamp (shared with dashboard)"
    )
    assert "clamp_response_hours_unix" in source, (
        "ibx0 must import clamp_response_hours_unix"
    )


# ── Slack resolution polling ────────────────────────────────────────────────

def test_email_uid_uses_rfc_message_id():
    """
    Bug: same email delivered to multiple accounts (or appearing as multiple
    Gmail messages in one account) showed up twice because _item_uid used the
    Gmail API message ID, which differs per copy. RFC Message-ID is the same
    for all copies of the same email.

    Fix: _item_uid for emails must prefer RFC Message-ID (email['message_id'])
    over the Gmail API ID, so duplicate copies are recognized as the same item.
    """
    source = IBX_ALL_PY.read_text()
    # Find _item_uid function body
    fn_start = source.index("def _item_uid(")
    fn_end = source.index("\ndef ", fn_start + 1)
    fn_body = source[fn_start:fn_end]
    assert "message_id" in fn_body, (
        "_item_uid must use RFC Message-ID for email dedup, not just Gmail API ID"
    )


def test_notify_sent_plays_sound_and_shows_indicator():
    """
    Bug: only iMessage played a sound on send. Other channels (Gmail, Slack,
    Teams, Outlook) gave no audible or visual feedback after replying.

    Fix: _notify_sent() plays a system sound and prints an envelope indicator.
    All reply confirmation paths must call it instead of bare console.print.
    """
    source = IBX_ALL_PY.read_text()
    assert "def _notify_sent(" in source, (
        "_notify_sent helper must exist"
    )
    assert "afplay" in source[source.index("def _notify_sent("):source.index("\ndef ", source.index("def _notify_sent(") + 1)], (
        "_notify_sent must play a system sound via afplay"
    )
    # No remaining bare "Sent + done" prints (all should use _notify_sent)
    assert 'console.print("[green]Sent + done.' not in source, (
        "All send confirmations must use _notify_sent(), not bare console.print"
    )


def test_poll_resolved_checks_slack():
    """
    Bug: _poll_resolved and check_resolved_now only checked email and imsg,
    never Slack. Archived Slack threads reappeared on every background fetch
    because they were never added to the resolved set.

    Fix: both functions must check Slack read state (last_read vs latest_ts).
    """
    source = IBX_ALL_PY.read_text()

    # _poll_resolved must handle slack
    poll_fn_start = source.index("def _poll_resolved(")
    poll_fn_end = source.index("\ndef ", poll_fn_start + 1)
    poll_body = source[poll_fn_start:poll_fn_end]
    assert '"slack"' in poll_body, (
        "_poll_resolved must check Slack items for resolution"
    )
    assert "last_read" in poll_body, (
        "_poll_resolved Slack check must compare last_read timestamp"
    )

    # check_resolved_now must handle slack
    check_fn_start = source.index("def check_resolved_now(")
    check_fn_end = source.index("\ndef ", check_fn_start + 1)
    check_body = source[check_fn_start:check_fn_end]
    assert '"slack"' in check_body, (
        "check_resolved_now must check Slack items for resolution"
    )
    assert "last_read" in check_body, (
        "check_resolved_now Slack check must compare last_read timestamp"
    )


def test_inbox_zero_marks_habits_done():
    """
    Bug: /inbound delegated to ibx0.main() for inbox processing, but ibx0
    never called /did to mark inbox habits done in neon (0₦). The /ibx0 skill
    (which marks habits) was never invoked — it's a separate Claude skill,
    not part of the Python ibx0.main() flow.

    Fix: ibx0.py must call _mark_inbox_habits_done() when inbox zero is
    achieved, which invokes /did to write to neon.
    """
    source = IBX_ALL_PY.read_text()

    # _mark_inbox_habits_done must exist
    assert "def _mark_inbox_habits_done(" in source, (
        "ibx0.py must define _mark_inbox_habits_done to mark inbox habits in neon"
    )

    # It must call /did with the inbox habits
    fn_start = source.index("def _mark_inbox_habits_done(")
    fn_end = source.index("\ndef ", fn_start + 1)
    fn_body = source[fn_start:fn_end]
    assert "/did" in fn_body, (
        "_mark_inbox_habits_done must invoke /did to mark habits in neon"
    )
    assert "ibx" in fn_body.lower(), (
        "_mark_inbox_habits_done must mark ibx-related habits"
    )

    # Both inbox-zero paths must call it
    # Path 1: initial empty inbox (if not all_items)
    # Path 2: drained all items in the card loop
    import re
    # Find call sites (not the definition)
    calls = [m.start() for m in re.finditer(r'(?<!\bdef )_mark_inbox_habits_done\(\)', source)]
    # Exclude the definition line itself
    def_pos = source.index("def _mark_inbox_habits_done(")
    calls = [c for c in calls if abs(c - def_pos) > 50]
    assert len(calls) >= 2, (
        f"ibx0.py must call _mark_inbox_habits_done() in both inbox-zero paths, "
        f"found {len(calls)} call site(s)"
    )
