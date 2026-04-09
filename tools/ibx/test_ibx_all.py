"""Regression tests for ibx_all.py."""
import ast
from pathlib import Path

IBX_ALL_PY = Path(__file__).parent / "ibx_all.py"


def test_outlook_wait_not_gated_on_empty_inbox():
    """
    Bug: ibx only waited for Outlook when all_items was empty.
    If Gmail/iMsg/Slack returned items, Outlook wait was skipped entirely.
    User would process other items and never see Outlook emails.

    Fix: always wait for Outlook if _outlook_done is not set, regardless
    of whether other sources returned items.
    """
    source = IBX_ALL_PY.read_text()

    # Find the "waiting for Outlook" block — it must NOT be gated on "not all_items"
    lines = source.splitlines()
    for i, line in enumerate(lines):
        if "waiting for Outlook" in line:
            # Check the if-condition on the line before (or same block)
            context = "\n".join(lines[max(0, i - 3):i + 1])
            assert "not all_items" not in context, (
                "Outlook wait must not be gated on 'not all_items' — "
                "it should always wait when _outlook_done is not set"
            )
            return
    raise AssertionError("'waiting for Outlook' block not found in ibx_all.py")
