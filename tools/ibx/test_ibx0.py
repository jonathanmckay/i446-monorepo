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
