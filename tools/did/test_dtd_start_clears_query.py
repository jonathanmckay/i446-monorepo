#!/usr/bin/env python3
"""Regression: "start a timer for a task, then navigate — keys get shunted
into the text input and show gibberish."

Every mutating fzf binding that does `deselect-all+reload(...)` also chains
`+clear-query` right after, EXCEPT ctrl-s (start timer), which was missing
it. Without clear-query, whatever text was sitting in fzf's search/filter
box before ctrl-s fired (a leftover search the user typed to find the task,
or a stray byte that slipped past the reset/drain race mitigation during the
Toggl network calls) stays in the box and keeps focus after the list
reloads — so the user's next keystrokes land in that box instead of moving
the list, which reads as "navigation got shunted into the text input."

Every sibling binding (enter, alt-enter, ctrl-d, ctrl-x, ctrl-p, ctrl-v,
ctrl-k) already clears the query on completion; ctrl-s is the one binding
of that class that didn't, a copy-paste omission.

Fix: add `+clear-query` to the ctrl-s bind, matching its siblings.
"""
import re
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()

# Bindings that mutate state via deselect-all+reload(...) and must not leave
# stale text sitting in the query box afterward.
DESELECT_RELOAD_KEYS = (
    "enter", "alt-enter", "ctrl-s", "ctrl-d", "ctrl-x",
    "ctrl-p", "ctrl-v", "ctrl-k",
)


def _bind_line(key: str) -> str:
    m = re.search(rf'--bind "{re.escape(key)}:[^"]*"', DTD)
    assert m, f'--bind "{key}:...\' not found'
    return m.group(0)


def test_ctrl_s_bind_exists_and_uses_deselect_reload():
    line = _bind_line("ctrl-s")
    assert "deselect-all" in line
    assert "reload($DTD_RELOAD)" in line


def test_every_deselect_reload_binding_clears_query():
    for key in DESELECT_RELOAD_KEYS:
        line = _bind_line(key)
        assert "clear-query" in line, (
            f'--bind "{key}:...\' does deselect-all+reload but never '
            "clear-query -- stale query text (a leftover search, or a byte "
            "that survived the reset/drain race) sticks around and eats "
            "the user's next keystrokes instead of navigating the list")


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
