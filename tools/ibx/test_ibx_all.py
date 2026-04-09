"""Regression tests for ibx_all.py."""
import ast
from pathlib import Path

IBX_ALL_PY = Path(__file__).parent / "ibx_all.py"


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
