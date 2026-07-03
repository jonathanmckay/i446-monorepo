"""Regression test: -1neon ritual cards completed BY NAME route through run_ritual.

The daemon creates each block's ritual cards as `😈 <tag>` (@-1neon). dtd's
enter/alt-enter worker completes tasks by piping the card NAME to
`did-fast.py "<name>"`. Before 2026-07-03 that generic path matched the card as
a plain Todoist task and closed it WITHOUT stamping the ritual emoji on the
block header in build-order.md — and since the daemon's boundary reconcile
scores manual rituals from the header stamps, the points were silently lost
(-1ibx completed in dtd: 辰/巳 got no 📧, -1₦ ran 3 short per block).

Functional half: lib/neon_blocks.ritual_card_tag resolves card names to tags
(and fails closed). Structural half (this repo's source-inspection style): the
did-fast main path intercepts ritual cards between parse and route, gates on
--points-only, journals no reopenable Todoist id, and refreshes the cache.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DID_FAST = HERE / "did-fast.py"

sys.path.insert(0, str(HERE.parent.parent / "lib"))
import neon_blocks as nb  # noqa: E402

# Injected config so the tests don't depend on the live block-rituals.json.
CFG = {
    "auto_marker": "😈",
    "rituals": [
        {"tag": "سمش", "emoji": "☀️", "points": 1, "mode": "manual"},
        {"tag": "-1g", "emoji": "🎯", "points": 3, "mode": "manual"},
        {"tag": "-1ibx", "emoji": "📧", "points": 3, "mode": "manual"},
        {"tag": "-1t", "emoji": "⏱️", "points": 3, "mode": "auto"},
        {"tag": "-1l", "emoji": "✅", "points": 3, "mode": "auto"},
    ],
}


# ── Functional: ritual_card_tag ──────────────────────────────────────────────

def test_daemon_card_names_resolve():
    assert nb.ritual_card_tag("😈 -1ibx", CFG) == "-1ibx"
    assert nb.ritual_card_tag("😈 سمش", CFG) == "سمش"
    assert nb.ritual_card_tag("😈 -1g", CFG) == "-1g"


def test_annotated_card_resolves():
    # User-edited card carrying (N)/[N] estimates (seen live 2026-07-03).
    assert nb.ritual_card_tag("😈 -1g (15) [15]", CFG) == "-1g"


def test_bare_tag_is_not_hijacked():
    # No 😈 marker → not a daemon card; the generic path must keep it.
    assert nb.ritual_card_tag("-1ibx", CFG) is None
    assert nb.ritual_card_tag("-1g", CFG) is None


def test_auto_rituals_never_match():
    # -1t/-1l are daemon-computed, never cards — even 😈-marked they must not route.
    assert nb.ritual_card_tag("😈 -1t", CFG) is None
    assert nb.ritual_card_tag("😈 -1l", CFG) is None


def test_ordinary_names_pass_through():
    assert nb.ritual_card_tag("ibx i9", CFG) is None
    assert nb.ritual_card_tag("😈", CFG) is None


def test_missing_marker_fails_closed():
    # An empty/absent auto_marker must disable matching entirely, not make the
    # marker check vacuous (any task containing token `-1g` would be hijacked).
    bare_cfg = {"auto_marker": "", "rituals": CFG["rituals"]}
    assert nb.ritual_card_tag("😈 -1g", bare_cfg) is None
    assert nb.ritual_card_tag("-1g", bare_cfg) is None
    no_marker_cfg = {"rituals": CFG["rituals"]}
    assert nb.ritual_card_tag("😈 -1g", no_marker_cfg) is None


# ── Structural: the did-fast main-path intercept is wired correctly ─────────

def _intercept_segment() -> tuple[str, str]:
    src = DID_FAST.read_text()
    i_parse = src.index("items = parse_input(raw)")
    i_intercept = src.index("ritual_card_tag(", i_parse)
    i_route = src.index("routes = route_items(", i_intercept)
    assert i_parse < i_intercept < i_route, (
        "ritual-card intercept must sit between parse_input and route_items")
    return src, src[src.index("# 1b.", i_parse):i_route]


def test_intercept_runs_run_ritual_before_routing():
    _src, seg = _intercept_segment()
    assert "run_ritual(" in seg, "intercepted cards must go through run_ritual"


def test_intercept_gated_on_points_only():
    # dtd's split flow calls --points-only and promises no Todoist side
    # effects; run_ritual is all side effects, so the intercept must not run.
    _src, seg = _intercept_segment()
    assert "if not points_only:" in seg


def test_ritual_entries_carry_no_reopenable_id():
    # undo-fast reopens any results entry with todoist.id, but nothing
    # un-stamps the block header — the entry must omit the id so ctrl-z
    # skips it instead of half-undoing (reopened card, stamped header).
    _src, seg = _intercept_segment()
    assert '"todoist": {"closed"' in seg
    assert '"id"' not in seg


def test_intercept_refreshes_cache_even_in_mixed_batch():
    # The cache mtime bump is dtd's only reload signal (regression
    # 2026-06-29); a mixed batch (`😈 -1ibx, 冥想`) must refresh too, not
    # just the all-ritual early return.
    _src, seg = _intercept_segment()
    assert "refresh_task_queue(block=True)" in seg
    assert seg.index("refresh_task_queue(block=True)") < seg.index("if not items:")


def test_output_seeds_ritual_entries():
    src, _seg = _intercept_segment()
    assert 'output = {"results": list(ritual_entries)' in src, (
        "mixed-batch output must include the ritual entries")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
