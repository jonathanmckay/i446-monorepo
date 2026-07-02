#!/usr/bin/env python3
"""Regression: an idle-open dtd that crosses midnight must show the NEW day's
tasks, not yesterday's.

Bug (2026-07-02): dtd's UI loop blocks on the fzf call, so its midnight-rollover
code (which advances $LOCAL_TODAY) never runs while the picker sits open. The
background watcher DID refresh the task cache at the day boundary, but its reload
command and completed-today overlay were built once at startup with the frozen
$LOCAL_TODAY. So after the cache refreshed with today's tasks, the watcher
reloaded the list filtered to *yesterday's* date, hiding the new day's tasks —
"new day, different tasks, but dtd didn't refresh."

Fix: the watcher recomputes the date each iteration ($watch_today) and uses it
for the reload cmd + overlay, and resets the per-day overlays + pulls the cache
on a day rollover. These assertions pin that the watcher never falls back to the
frozen startup date.
"""
import re
from pathlib import Path

DTD = Path(__file__).resolve().parent / "dtd.sh"


def _watcher_block() -> str:
    """The background auto-reload watcher subshell (between its header comment
    and WATCHER_PID=). This is the code that runs while dtd sits idle-open."""
    src = DTD.read_text()
    start = src.index("# Auto-reload watcher:")
    end = src.index("WATCHER_PID=", start)
    return src[start:end]


def _watcher_code() -> str:
    """The watcher block with comment-only lines stripped, so assertions about
    what the code *does* aren't tripped by explanatory comments that name the
    old symbol."""
    lines = [ln for ln in _watcher_block().splitlines()
             if not ln.lstrip().startswith("#")]
    return "\n".join(lines)


def test_watcher_recomputes_today_each_iteration():
    block = _watcher_block()
    assert 'watch_today="$(date +%Y-%m-%d)"' in block, (
        "watcher must recompute today's date inside the loop, not freeze it")


def test_watcher_reload_uses_fresh_date_not_startup():
    block = _watcher_block()
    # The reload posted to fzf must filter by the freshly-computed date.
    assert "reload($watch_reload)" in block
    assert "'$watch_today'" in block, (
        "the rebuilt reload cmd must pass $watch_today to the list generator")


def test_watcher_never_uses_frozen_local_today():
    """The whole point of the bug: the watcher baked in the startup $LOCAL_TODAY.
    It must not reference it anywhere now (reload cmd or overlay)."""
    code = _watcher_code()
    assert "$LOCAL_TODAY" not in code, (
        "watcher code still references the frozen startup date $LOCAL_TODAY")
    assert "DTD_WATCH_RELOAD" not in code, (
        "the static startup-dated reload var must be gone")


def test_watcher_handles_day_rollover():
    """On a day change the watcher resets the per-day overlays and pulls the new
    day's tasks (the UI loop can't, being blocked on fzf)."""
    block = _watcher_block()
    assert 'if [[ "$watch_today" != "$last_day" ]]' in block, (
        "watcher must detect a day rollover")
    # Session/journal reset + a cache refresh so the new day's list is correct.
    assert ': > "$DTD_SESSION"' in block
    assert "--refresh-cache" in block


def test_overlay_uses_fresh_date():
    """The completed-today overlay jq must key off $watch_today, and none of the
    watcher's overlay lines may still use the old '--arg t \"$LOCAL_TODAY\"'."""
    block = _watcher_block()
    assert '--arg t "$watch_today"' in block
    assert '--arg t "$LOCAL_TODAY"' not in block
