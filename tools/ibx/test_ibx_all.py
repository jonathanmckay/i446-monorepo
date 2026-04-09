"""Regression tests for ibx_all.py."""
import ast
from pathlib import Path

IBX_ALL_PY = Path(__file__).parent / "ibx_all.py"


def test_outlook_wait_not_gated_on_empty_inbox():
    """
    Bug: ibx declares 'Inbox zero' before slow sources (Outlook/Teams) finish loading.
    Fast sources (Gmail/iMsg/Slack) return 0 items within 8s, then ibx exits immediately
    without waiting for Outlook/Teams which take 60-120s via workiq.

    Fix: when all_items is empty and _fetch_done is not set, wait for slow sources.
    """
    source = IBX_ALL_PY.read_text()

    # The code must wait for slow sources before declaring inbox zero
    lines = source.splitlines()
    found_wait = False
    for i, line in enumerate(lines):
        if "waiting for" in line and ("outlook" in line.lower() or "slow" in line.lower() or "pending" in line.lower()):
            # Verify this wait block comes BEFORE the inbox zero declaration
            for j in range(i + 1, min(i + 20, len(lines))):
                if "Inbox zero" in lines[j]:
                    found_wait = True
                    break
            if found_wait:
                break
    assert found_wait, (
        "ibx_all.py must wait for slow sources (Outlook/Teams) before declaring Inbox zero"
    )
