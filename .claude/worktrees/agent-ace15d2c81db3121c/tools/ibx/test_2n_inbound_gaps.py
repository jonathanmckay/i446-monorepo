"""Regression test for /inbound card ordering.

Bug (2026-06-27): /inbound surfaced the current block's goal (-1g) card only
AFTER the gap-audit cards for previous blocks ("你HHMM做了什么?"), so the goal
prompt felt buried "after finishing the queue." Those previous-block prompts
should be swallowed in /inbound (skip_comms) so the goal card leads. /-2n keeps
the gap audit.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("_two_n", HERE / "-2n.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


m = _load()


class _DummyThread:
    def __init__(self, *a, **k): pass
    def start(self): pass


def _stub(monkeypatch, seen_titles):
    monkeypatch.setattr(m, "set_term_color", lambda c: None)
    monkeypatch.setattr(m, "snapshot_build_order", lambda: None)
    monkeypatch.setattr(m, "check_neon_column", lambda c: "done")
    monkeypatch.setattr(m, "has_prayer_marker", lambda b: True)      # no salah card
    monkeypatch.setattr(m, "daily_habits_due_today", lambda: False)  # no habits card
    monkeypatch.setattr(m, "is_asleep_now", lambda *a, **k: False)
    monkeypatch.setattr(m, "fetch_block_suggestions", lambda *a, **k: [])
    monkeypatch.setattr(m, "fetch_suggested_goals", lambda *a, **k: [])
    monkeypatch.setattr(m, "spawn_1g_background", lambda t: None)
    monkeypatch.setattr(m, "spawn_ate_background", lambda t: None)
    monkeypatch.setattr(m, "append_block_goals", lambda *a, **k: True)
    monkeypatch.setattr(m, "write_block_goals", lambda *a, **k: True)
    # We're in 午 (idx 4): four previous blocks exist...
    monkeypatch.setattr(m, "get_current_block", lambda: (4, "午", "12:00", "13:59"))
    # ...and every one of them has an unfilled gap.
    monkeypatch.setattr(m, "check_time_gaps", lambda *a, **k: [("06:30", "06:45")])
    # No goals yet for the current block, so the goal card must fire.
    monkeypatch.setattr(m, "read_block_goals_with_status", lambda: {})
    monkeypatch.setattr(m.threading, "Thread", _DummyThread)

    def _interrupt(*a, **k):
        # Breaks the post-cards idle loop (its time.sleep) and the "Start timer?"
        # prompt, so main() returns instead of spinning forever.
        raise KeyboardInterrupt

    monkeypatch.setattr(m.time, "sleep", _interrupt)
    monkeypatch.setattr(m.console, "input", _interrupt)

    def fake_prompt(card_num, total, title, body, **k):
        seen_titles.append(title)
        return "skip"

    monkeypatch.setattr(m, "prompt_card", fake_prompt)


def test_inbound_swallows_previous_block_gap_prompts(monkeypatch):
    titles = []
    _stub(monkeypatch, titles)
    m.main(skip_comms=True)
    assert not any("Gaps" in t for t in titles), \
        f"/inbound should swallow previous-block gap prompts, saw: {titles}"
    assert any(t.strip() == "-1g" for t in titles), \
        f"/inbound must still show the goal card, saw: {titles}"
    # And the goal card should LEAD (no other interactive prompt before it).
    goal_idx = next(i for i, t in enumerate(titles) if t.strip() == "-1g")
    assert goal_idx == 0, f"goal card should be first in /inbound, order: {titles}"


def test_dash2n_keeps_gap_audit_structurally():
    """/-2n (skip_comms False) must still gather gaps — the swallow is gated on
    skip_comms, not unconditional."""
    src = (HERE / "-2n.py").read_text()
    assert re.search(r"if idx > 0 and not skip_comms:", src), \
        "gap gathering must be gated on `not skip_comms` (keep it for /-2n)"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
