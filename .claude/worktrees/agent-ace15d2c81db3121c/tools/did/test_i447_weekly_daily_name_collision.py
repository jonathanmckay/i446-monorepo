#!/usr/bin/env python3
"""Regression (2026-08-01): "I've marked i447 done 3 times today and it's
not staying done."

"i447" is BOTH a daily 0n header (column H) and a bare weekly 1n+ header
(column AI, card content "i447 (20) [25]", label 1neon) -- no "1 " prefix,
unlike most weekly cards. Step 0.1 (0n match) is a hard name-based gate that
always wins when a name collides, and it terminates the item's routing
(`continue`) before Step 0.2 (1n+) ever runs. So completing the WEEKLY card
by name always got routed through the DAILY 0n branch instead.

That branch's own Todoist-close is additionally gated to `require_labels=
{"0neon","夜neon"}` (see match_todoist_task's preferred_id fallback) -- the
weekly card carries "1neon", not those, so even with dtd's preferred_id
naming the exact selected row, the close was silently skipped. The 0n column
got written every time (harmlessly, since it's a flat "1" flag, not
cumulative) but the actual weekly Todoist card was NEVER closed -- it just
sat open, resurfacing in dtd every time it was "completed" (confirmed live:
task 6h8rcv7H7J9XGq6Q, due 2026-08-01, still open despite three same-day
completion attempts).

Fix: when preferred_id resolves to a task in the 1neon bucket, that item
isn't a 0n completion no matter what its bare name collides with -- skip
Step 0.1 and let it fall through to Step 0.2, which correctly requires the
1neon label.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "did_fast_i447", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_i447"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def df():
    return _load()


HEADERS = {"0n": {"i447": "H"}, "1n": {"i447": "AI"}}
WEEKLY_TASK = {"id": "6h8rcv7H7J9XGq6Q", "content": "i447 (20) [25]",
               "labels": ["1neon", "i447"], "due": "2026-08-01",
               "recurring": True}
DAILY_TASK = {"id": "6gHVV7fjPwqfvq76", "content": "i447 (15) [5]",
              "labels": ["0neon", "i447"], "due": "2026-08-01",
              "recurring": True}


def _tq(weekly=True, daily=False):
    return {
        "0neon": [DAILY_TASK] if daily else [],
        "夜neon": [],
        "1neon": [WEEKLY_TASK] if weekly else [],
        "today": [],
    }


def test_weekly_card_selected_by_id_routes_to_1n_not_0n(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 30)
    items = df.parse_input("i447")
    routes = df.route_items(items, HEADERS, _tq(), skip_todoist=True,
                            preferred_id=WEEKLY_TASK["id"])
    assert len(routes) == 1
    assert routes[0].step == "1n", (
        "selecting the WEEKLY card by id must route through the 1n+ branch, "
        "not fall into the daily 0n branch just because the bare name "
        "collides with the 0n header")
    assert routes[0].col_letter == "AI"


def test_weekly_card_close_uses_the_1neon_bucket_not_0neon(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 30)
    items = df.parse_input("i447")
    routes = df.route_items(items, HEADERS, _tq(), skip_todoist=False,
                            preferred_id=WEEKLY_TASK["id"])
    r = routes[0]
    assert r.todoist_task is not None, (
        "the weekly card must actually be found for closing -- this is the "
        "concrete symptom: previously the card was silently never closed "
        "and kept resurfacing as still-open")
    assert r.todoist_task["id"] == WEEKLY_TASK["id"]


def test_daily_card_selected_by_id_still_routes_to_0n(df, monkeypatch):
    """Preserve existing behavior: selecting the actual DAILY card must
    still hit the 0n branch as before -- the fix must not blanket-disable
    Step 0.1 for the name "i447", only when preferred_id says otherwise."""
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 1)
    items = df.parse_input("i447")
    routes = df.route_items(items, HEADERS, _tq(daily=True), skip_todoist=True,
                            preferred_id=DAILY_TASK["id"])
    assert routes[0].step == "0n"


def test_no_preferred_id_still_defaults_to_0n(df, monkeypatch):
    """Typing "i447" by hand (dtd not involved, no preferred_id) must keep
    defaulting to the daily habit -- unchanged from before this fix."""
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 1)
    items = df.parse_input("i447")
    routes = df.route_items(items, HEADERS, _tq(), skip_todoist=True,
                            preferred_id=None)
    assert routes[0].step == "0n"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
