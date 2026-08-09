"""Regression (user report 2026-07-28: "player retention still in todoist
but not in dtd ... maybe dtd waits until close to clear these tasks off of
todoist which seems too long of a wait"): dtd's FIFO worker is strictly
serial and each completion runs the full did-fast pipeline (Excel over ssh,
5-45s) with the Todoist close LAST, so a completion burst left later cards
open in Todoist for minutes while dtd had already hidden them optimistically.

quick-close.py fires the close at ⌃⏎ time for NON-recurring tasks only —
closing a recurring card twice would double-advance its recurrence (the
2026-06-27 due-date-drift class), so those keep the guarded pipeline order.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("quick_close", HERE / "quick-close.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_closes_only_provable_non_recurring():
    m = _load()
    data = {
        "today": [
            {"id": "T1", "content": "read player retention (15) [15]",
             "labels": ["i9"], "due": "2026-07-28", "recurring": False},
            {"id": "R1", "content": "hiit (10) [23]",
             "labels": ["0neon"], "due": "2026-07-28", "recurring": True},
        ],
        "1neon": [
            {"id": "W1", "content": "1 i9 (10) [40]",
             "labels": ["1neon"], "due": "2026-08-03", "recurring": "every Monday"},
        ],
    }
    assert m.should_close(data, "T1") is True
    assert m.should_close(data, "R1") is False, "recurring must never quick-close"
    assert m.should_close(data, "W1") is False, "truthy recurring strings count as recurring"
    assert m.should_close(data, "UNKNOWN") is False, \
        "an id absent from the cache is not provably one-off — skip"


def test_best_effort_never_raises(tmp_path):
    """A garbage cache path or malformed json must exit 0 silently — the
    pipeline close is the reliable path."""
    m = _load()
    import subprocess
    r = subprocess.run([sys.executable, str(HERE / "quick-close.py"),
                        "T1", str(tmp_path / "nope.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0 and not r.stderr


def test_dtd_wires_quick_close_into_the_completion_path():
    """Structural: the (sole) completion script, done.sh, must fire
    quick-close after the optimistic id-hide, backgrounded so ⌥⏎ never
    blocks on the network. enter.sh no longer has a completion branch at all
    (2026-07-31: "always opt+enter to mark done"), so it no longer has its
    own copy of this call."""
    src = (HERE / "dtd.sh").read_text()
    assert src.count("quick-close.py") == 1, "done.sh only"
    # Backgrounded subshell, output discarded — alt-enter must never block on the network.
    assert src.count('quick-close.py" "\\$1" "$DTD_CACHE_FILE" >/dev/null 2>&1 &') == 1


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
