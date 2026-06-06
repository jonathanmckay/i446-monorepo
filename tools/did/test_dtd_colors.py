#!/usr/bin/env python3
"""Regression: dtd's COLORS palette must cover every project in the canonical
tg-tui PROJECT_COLORS palette, with matching RGB values.

Bug (2026-06-06): 家-labeled tasks (e.g. 一起饭) rendered colorless in dtd —
dtd's palette was missing 家 and 睡觉, which tg-tui (sourced from
vault/i447/neon-color-pallette.md) defines.
"""
import re
from pathlib import Path

DTD = Path(__file__).resolve().parent / "dtd.sh"
TG_TUI = Path(__file__).resolve().parent.parent / "tg" / "tg-tui.py"


def _dtd_colors() -> dict:
    src = DTD.read_text()
    block = src[src.index("COLORS = {"):]
    block = block[:block.index("}")]
    return {
        m.group(1): tuple(int(x) for x in m.group(2).split(";"))
        for m in re.finditer(
            r"'([^']+)':\s*'\\033\[38;2;(\d+;\d+;\d+)m'", block)
    }


def _tg_tui_colors() -> dict:
    src = TG_TUI.read_text()
    block = src[src.index("PROJECT_COLORS = {"):]
    block = block[:block.index("}")]
    out = {}
    for m in re.finditer(r'"([^"]+)":\s*"#([0-9a-fA-F]{6})"', block):
        h = m.group(2)
        out[m.group(1)] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return out


def test_dtd_palette_covers_canonical_palette():
    dtd, tui = _dtd_colors(), _tg_tui_colors()
    assert tui, "failed to parse tg-tui palette"
    missing = sorted(set(tui) - set(dtd))
    assert not missing, f"dtd COLORS missing projects from canonical palette: {missing}"


def test_dtd_palette_rgb_matches_canonical():
    dtd, tui = _dtd_colors(), _tg_tui_colors()
    mismatched = {k: (dtd[k], tui[k]) for k in tui if k in dtd and dtd[k] != tui[k]}
    assert not mismatched, f"dtd colors diverge from canonical palette: {mismatched}"


def test_jia_specifically_present():
    """The trigger case: 家 must be colored (Ferrari #ff4136)."""
    assert _dtd_colors().get("家") == (255, 65, 54)
