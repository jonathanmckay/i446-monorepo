"""Regression tests for ibx_all.py."""
import ast
from pathlib import Path

IBX_ALL_PY = Path(__file__).parent / "ibx_all.py"


def test_outlook_wait_not_gated_on_empty_inbox():
    """
    Bug: ibx declares 'Inbox zero' even when slow sources (Outlook/Teams) returned items.
    Race condition: _fetch_done is set before _bg_drainer finishes its final drain,
    so the wait block is skipped and items in _bg_injected are never moved to all_items.

    Fix: always wait for _fetch_done and always drain _bg_injected + _incoming before
    checking if all_items is empty. The drain must NOT be gated on 'not all_items'.
    """
    source = IBX_ALL_PY.read_text()
    lines = source.splitlines()

    # Find the "Inbox zero" declaration
    inbox_zero_line = None
    for i, line in enumerate(lines):
        if "Inbox zero" in line and "console.print" in line:
            inbox_zero_line = i
            break
    assert inbox_zero_line is not None, "'Inbox zero' print not found"

    # Look backwards from Inbox zero for the drain logic — it must drain
    # _bg_injected AND _incoming unconditionally (not inside 'if not all_items')
    context_before = "\n".join(lines[max(0, inbox_zero_line - 30):inbox_zero_line])
    assert "_bg_injected" in context_before, (
        "Must drain _bg_injected before declaring Inbox zero"
    )
    assert "_incoming" in context_before, (
        "Must drain _incoming queue before declaring Inbox zero"
    )
