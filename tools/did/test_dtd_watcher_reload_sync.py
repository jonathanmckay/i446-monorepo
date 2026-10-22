#!/usr/bin/env python3
"""Regression (2026-10-22): "I wasn't able to delay a currently running task
in dtd."

A running Toggl timer makes dtd's background watcher reload the list every
~1 s so the spinner row advances. That POST used fzf's async `reload`, which
empties the match list while the new input re-streams. With a search query
active, a ctrl-d/ctrl-v pressed in that window had no current row, so fzf
skipped the action with no event logged (headless repro: 5/40 presses landed
under `reload`, 40/40 under `reload-sync`). The watcher must use
`reload-sync`, which keeps the old rows actionable until the new list is in.
"""
import re
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _watcher_posts():
    return re.findall(r'--data "(reload[-a-z]*)\(\$watch_reload\)', DTD)


def test_watcher_reload_posts_exist():
    # both the API-key and the plain curl branch
    assert len(_watcher_posts()) == 2


def test_watcher_uses_reload_sync_not_async_reload():
    assert set(_watcher_posts()) == {"reload-sync"}, _watcher_posts()
