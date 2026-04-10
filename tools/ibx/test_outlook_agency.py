"""Regression tests for outlook_agency."""
import ast
from pathlib import Path

OUTLOOK_AGENCY_PY = Path(__file__).parent / "outlook_agency.py"


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
