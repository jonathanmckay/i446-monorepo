#!/usr/bin/env python3
"""Regression: dtd's COLORS palette must cover every project in the canonical
janus PROJECT_COLORS palette, with matching RGB values.

Bug (2026-06-06): 家-labeled tasks (e.g. 一起饭) rendered colorless in dtd —
dtd's palette was missing 家 and 睡觉, which janus (sourced from
vault/i447/neon-color-pallette.md) defines.
"""
import re
from pathlib import Path

DTD = Path(__file__).resolve().parent / "dtd.sh"
JANUS = Path(__file__).resolve().parent.parent / "tg" / "janus.py"


def _dtd_colors() -> dict:
    src = DTD.read_text()
    block = src[src.index("COLORS = {"):]
    block = block[:block.index("}")]
    return {
        m.group(1): tuple(int(x) for x in m.group(2).split(";"))
        for m in re.finditer(
            r"'([^']+)':\s*'\\033\[38;2;(\d+;\d+;\d+)m'", block)
    }


def _janus_colors() -> dict:
    src = JANUS.read_text()
    block = src[src.index("PROJECT_COLORS = {"):]
    block = block[:block.index("}")]
    out = {}
    for m in re.finditer(r'"([^"]+)":\s*"#([0-9a-fA-F]{6})"', block):
        h = m.group(2)
        out[m.group(1)] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return out


def test_dtd_palette_covers_canonical_palette():
    dtd, tui = _dtd_colors(), _janus_colors()
    assert tui, "failed to parse janus palette"
    missing = sorted(set(tui) - set(dtd))
    assert not missing, f"dtd COLORS missing projects from canonical palette: {missing}"


def test_dtd_palette_rgb_matches_canonical():
    dtd, tui = _dtd_colors(), _janus_colors()
    mismatched = {k: (dtd[k], tui[k]) for k in tui if k in dtd and dtd[k] != tui[k]}
    assert not mismatched, f"dtd colors diverge from canonical palette: {mismatched}"


def test_jia_specifically_present():
    """The trigger case: 家 must be colored. Pool Party #00b8d4 (teal) since
    2026-07-05 — it was Ferrari red before, which read as orange in dtd and
    contradicted the user's mental model of 家 = teal."""
    assert _dtd_colors().get("家") == (0, 184, 212)


def test_ritual_cards_get_domain_colors():
    """-1neon cards carry no domain label; dtd maps the ritual tag to a domain
    color (2026-07-07): -1ibx→i9, -1l→g245, -1t→n156, سمش→hcm."""
    src = DTD.read_text()
    assert "RITUAL_DOMAIN = {'-1ibx': 'i9', '-1l': 'g245', '-1t': 'n156', 'سمش': 'hcm'}" in src
    assert "'-1neon' in t.get('labels', [])" in src, (
        "list builder must resolve ritual-card color from the tag name")
