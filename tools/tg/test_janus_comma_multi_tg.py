"""User request 2026-08-05: "make it so that comma delimits multiple /tg
calls from the janus text input box" — typing "a, b, c" in janus's bottom
input should fire three separate tg-fast.py calls (start/create/etc, one
per part), not a single literal "a, b, c" description. Parts run
sequentially (not concurrently, to avoid racing Toggl trim/split calls
against each other), and each part still gets the SAME per-part day-offset
resolution (viewed-day --date append / live-command rejection) a lone
typed command already got before this change.

User request 2026-08-20: a completed range carrying an explicit [N] points
annotation ("1815-1843 desc [30]") must route through did-fast instead of
tg-fast — tg-fast has no concept of points at all, so the [30] previously
just sat in the Toggl description doing nothing, silently crediting zero
points. _resolve_part now returns (command, use_did) instead of a bare
string; the tests below were updated for that shape.
"""
import re
import textwrap
from pathlib import Path

HERE = Path(__file__).parent


def _resolve_part_fn():
    """Extract the real `_has_completed_range`/`_resolve_part` closures'
    source out of the Enter handler and exec them standalone, so this test
    runs the ACTUAL resolution logic (day-offset --date append / did-vs-tg
    routing / live-command rejection) rather than just asserting strings
    are present — mirrors the extract-and-run pattern used elsewhere in
    this repo (e.g. xk887's router generation)."""
    src = (HERE / "janus.py").read_text()
    start = src.index("    def _has_completed_range(part: str) -> bool:")
    end = src.index("\n    resolved_pairs = [r for r in", start)
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
    assert resolve_part("meeting prep") == ("meeting prep", False)
    assert not calls["flash"]


def test_resolve_part_appends_viewed_date_to_a_range_on_past_day():
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = -1
    cmd, use_did = resolve_part("lunch 9-10")
    assert cmd.startswith("lunch 9-10 --date ")
    assert use_did is False, "no [N] annotation — must stay on tg-fast"
    assert not calls["flash"]


def test_resolve_part_rejects_a_plain_start_on_past_day():
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = -1
    assert resolve_part("meeting prep") is None
    assert any("HHMM-HHMM" in m for m in calls["flash"])


def test_resolve_part_allows_live_commands_on_past_day():
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = -1
    assert resolve_part("stop") == ("stop", False)
    assert resolve_part("current") == ("current", False)
    assert resolve_part("del 123") == ("del 123", False)
    assert not calls["flash"]


# ─── did-fast routing for a completed range + [N] points (2026-08-20) ──────

def test_resolve_part_routes_range_with_points_to_did():
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = 0
    cmd, use_did = resolve_part("call with josh 1815-1843 [30]")
    assert cmd == "call with josh 1815-1843 [30]", "command text itself is untouched"
    assert use_did is True


def test_resolve_part_range_without_points_stays_on_tg():
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = 0
    cmd, use_did = resolve_part("call with josh 1815-1843")
    assert use_did is False


def test_resolve_part_points_without_range_stays_on_tg():
    """A bare backdated start ("1823 desc [30]") has no completed end yet —
    the activity isn't done, so it must not route to did-fast just because
    a [N] happens to appear in the text."""
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = 0
    cmd, use_did = resolve_part("1823 desc [30]")
    assert use_did is False


def test_resolve_part_range_with_points_on_past_day_uses_md_token_not_dashdate():
    """did-fast reads its target date as a trailing "M/D" token, not tg-fast's
    "--date YYYY-MM-DD" flag — routing to did-fast on a viewed past day must
    use did-fast's own date shape."""
    state, resolve_part, calls = _resolve_part_fn()
    state.day_offset = -1
    cmd, use_did = resolve_part("call with josh 1815-1843 [30]")
    assert use_did is True
    assert "--date" not in cmd, "must not carry tg-fast's date flag when routed to did-fast"
    assert cmd.startswith("call with josh 1815-1843 [30] ")
    # trailing token must be M/D (no leading zero, matching did-fast's own
    # parse_input: f"{month}/{day}")
    assert re.search(r"\s\d{1,2}/\d{1,2}$", cmd), cmd


# ─── comma-split wiring in the Enter handler itself ─────────────────────────

def test_comma_splits_into_multiple_tg_calls():
    src = (HERE / "janus.py").read_text()
    i = src.index('parts = [p.strip() for p in text.split(",")')
    body = src[i:i + 4200]
    assert "if p.strip()" in src[i:src.index("\n", i)], (
        "empty segments (stray/trailing commas) must be dropped")
    assert "_resolve_part" in body, (
        "each comma-separated part must go through the same day-offset "
        "resolution a lone typed command would get")
    assert "for cmd, is_did in resolved_pairs:" in body, (
        "resolved parts must each be run through the did-vs-tg runner switch")
    assert "runner = run_did_fast if is_did else run_tg_fast" in body


def test_comma_split_parts_run_sequentially_not_concurrently():
    """Parts must be awaited one at a time inside a plain for-loop, not
    fired concurrently (e.g. via asyncio.gather) -- concurrent tg-fast.py/
    did-fast.py calls could race trimming/splitting the same Toggl entries."""
    src = (HERE / "janus.py").read_text()
    i = src.index("async def _run_and_refresh():", src.index('parts = [p.strip()'))
    body = src[i:i + 900]
    assert "gather" not in body and "create_task" not in body.split("event.app.create_background_task(_run_and_refresh())")[0][-900:]
    assert "for cmd, is_did in resolved_pairs:" in body


def test_comma_split_flash_reports_every_result():
    """With more than one part, the post-run flash must show ALL results
    joined together, not silently overwrite with just the last one."""
    src = (HERE / "janus.py").read_text()
    i = src.index("async def _run_and_refresh():", src.index('parts = [p.strip()'))
    body = src[i:i + 900]
    assert '" | ".join(results)' in body


def test_single_part_input_is_unaffected_by_the_split():
    """A plain (no-comma) command must behave exactly as before: `parts`
    degrades to a one-element list, `resolved_pairs` likewise, and the
    pre-run flash shows the bare command (no ' | ' separator noise)."""
    src = (HERE / "janus.py").read_text()
    i = src.index('parts = [p.strip() for p in text.split(",") if p.strip()] or [text]')
    assert i != -1, "single-item fallback (`or [text]`) must survive an all-empty split"
    j = src.index('flash(f"$ {\' | \'.join(labels)}"', i)
    line = src[j:src.index("\n", j)]
    assert "len(labels) > 1" in line, (
        "a single part must flash the bare command, not join a 1-item list")


def test_flash_preview_labels_did_vs_tg_per_part():
    """The pre-run flash line must show 'did'/'tg' per part depending on
    routing, not a hardcoded '$ tg' prefix (2026-08-20 -- with the new
    did-fast routing, a mixed batch could have parts going to either)."""
    src = (HERE / "janus.py").read_text()
    i = src.index('parts = [p.strip() for p in text.split(",")')
    body = src[i:i + 4200]
    assert "labels = [f\"{'did' if d else 'tg'} {c}\" for c, d in resolved_pairs]" in body


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
