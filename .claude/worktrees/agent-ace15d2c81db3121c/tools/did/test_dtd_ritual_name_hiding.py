#!/usr/bin/env python3
"""Regression: recurring -1neon ritual cards must be hidden by ID only, never by
name. They recur every 2h block with identical names (😈 سمش / -1g / -1ibx …),
so name-based hiding suppressed the CURRENT block's fresh card once an earlier
block's same-named card was completed/skipped that day (bug 2026-07-10: prayer
and -1g vanished for the rest of the day after being done once).
"""
import re
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _list_gen_block() -> str:
    # the reloadable list-generator heredoc
    i = DTD.index("# Build task list in priority order")
    j = DTD.index("skipped_lines", i)
    return DTD[DTD.rindex("raw = t['content']", 0, i) if False else i-4000:j]


def test_rituals_exempt_from_name_hiding():
    src = DTD
    assert "is_ritual = '-1neon' in t.get('labels', [])" in src
    # the name/removed hide must be gated on `not is_ritual`
    assert "if not is_ritual and (clean in name_only_completed" in src, (
        "name/removed hiding must be skipped for -1neon ritual cards")


def test_id_hide_still_applies_to_all():
    # the id-based hide (line above the gate) must remain ungated so a ritual's
    # own completed id still removes that specific card.
    src = DTD
    i = src.index("Hide by id first")
    seg = src[i:i+200]
    assert "str(t['id']) in completed_ids" in seg
