"""User request 2026-08-05: "make it so that comma delimits multiple /tg
calls from the janus text input box" — typing "a, b, c" in janus's bottom
input should fire three separate tg-fast.py calls (start/create/etc, one
per part), not a single literal "a, b, c" description. Parts run
sequentially (not concurrently, to avoid racing Toggl trim/split calls
against each other), and each part still gets the SAME per-part day-offset
resolution (viewed-day --date append / live-command rejection) a lone
typed command already got before this change.
"""
import re
import textwrap
from pathlib import Path

HERE = Path(__file__).parent


def _resolve_part_fn():
    """Extract the real `_resolve_part` closure's source out of the Enter
    handler and exec it standalone, so this test runs the ACTUAL resolution
    logic (day-offset --date append / live-command rejection) rather than
    just asserting strings are present — mirrors the extract-and-run
    pattern used elsewhere in this repo (e.g. xk887's router generation)."""
    src = (HERE / "janus.py").read_text()
    start = src.index("    def _resolve_part(part: str) -> str | None:")
    end = src.index("\n    resolved = [r for r in", start)
    body = "from __future__ import annotations\n" + textwrap.dedent(src[start:end])

    calls = {"flash": []}

    class _State:
        day_offset = 0

    import datetime as _dt

    ns = {
        "STATE": _State(),
        "flash": lambda msg, secs=4.0: calls["flash"].append(msg),
        "re": re,
        "view_now": lambda: _dt.datetime(2026, 7, 26, 12, 0, 0),
    }
    exec(compile(body, "<_resolve_part>", "exec"), ns)
    return ns["STATE"], ns["_resolve_part"], calls


def test_resolve_part_passthrough_when_viewing_today():
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = 0
    assert resolve_part("meeting prep") == "meeting prep"
    assert not calls["flash"]


def test_resolve_part_appends_viewed_date_to_a_range_on_past_day():
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = -1
    out = resolve_part("lunch 9-10")
    assert out is not None and out.startswith("lunch 9-10 --date ")
    assert not calls["flash"]


def test_resolve_part_rejects_a_plain_start_on_past_day():
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = -1
    assert resolve_part("meeting prep") is None
    assert any("HHMM-HHMM" in m for m in calls["flash"])


def test_resolve_part_allows_live_commands_on_past_day():
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = -1
    assert resolve_part("stop") == "stop"
    assert resolve_part("current") == "current"
    assert resolve_part("del 123") == "del 123"
    assert not calls["flash"]


# ─── comma-split wiring in the Enter handler itself ─────────────────────────

def test_comma_splits_into_multiple_tg_calls():
    src = (HERE / "janus.py").read_text()
    i = src.index('parts = [p.strip() for p in text.split(",")')
    body = src[i:i + 2200]
    assert "if p.strip()" in src[i:src.index("\n", i)], (
        "empty segments (stray/trailing commas) must be dropped")
    assert "_resolve_part" in body, (
        "each comma-separated part must go through the same day-offset "
        "resolution a lone typed command would get")
    assert "for cmd in resolved:" in body and "run_tg_fast, cmd" in body, (
        "resolved parts must each be run through run_tg_fast")


def test_comma_split_parts_run_sequentially_not_concurrently():
    """Parts must be awaited one at a time inside a plain for-loop, not
    fired concurrently (e.g. via asyncio.gather) -- concurrent tg-fast.py
    calls could race trimming/splitting the same Toggl entries."""
    src = (HERE / "janus.py").read_text()
    i = src.index("async def _run_and_refresh():", src.index('parts = [p.strip()'))
    body = src[i:i + 700]
    assert "gather" not in body and "create_task" not in body.split("event.app.create_background_task(_run_and_refresh())")[0][-700:]
    assert "for cmd in resolved:" in body


def test_comma_split_flash_reports_every_result():
    """With more than one part, the post-run flash must show ALL results
    joined together, not silently overwrite with just the last one."""
    src = (HERE / "janus.py").read_text()
    i = src.index("async def _run_and_refresh():", src.index('parts = [p.strip()'))
    body = src[i:i + 700]
    assert '" | ".join(results)' in body


def test_single_part_input_is_unaffected_by_the_split():
    """A plain (no-comma) command must behave exactly as before: `parts`
    degrades to a one-element list, `resolved` likewise, and the pre-run
    flash shows the bare command (no ' | ' separator noise)."""
    src = (HERE / "janus.py").read_text()
    i = src.index('parts = [p.strip() for p in text.split(",") if p.strip()] or [text]')
    assert i != -1, "single-item fallback (`or [text]`) must survive an all-empty split"
    j = src.index("flash(f\"$ tg {' | '.join(resolved)}\"", i)
    line = src[j:src.index("\n", j)]
    assert "len(resolved) > 1" in line, (
        "a single part must flash the bare command, not join a 1-item list")


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
