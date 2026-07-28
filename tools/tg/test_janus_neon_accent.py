"""Regression: the -1₦ block score renders as a BARE number in the red chip
style (NEON_PTS_STYLE: red background, white text — user request 2026-07-21,
replacing the lime ₦N accent, "no need for the fancy ₦ letter as it takes up
a character") at the RIGHT edge of the block line — beside the 分, not after
the block name — in every live header path: the compact block header (past
and future-head branches) and the 卯 sleep line."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_neon", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_neon"] = mod
    spec.loader.exec_module(mod)
    return mod


def _score_fragments(frags, mod):
    return [(sty, txt) for sty, txt in frags if sty == mod.NEON_PTS_STYLE]


def test_score_style_is_red_bg_white_text():
    mod = _load_tui()
    assert "bg:" in mod.NEON_PTS_STYLE and "#ffffff" in mod.NEON_PTS_STYLE
    # The lime fg accent must not style the score anywhere.
    assert mod.NEON_ACCENT not in mod.NEON_PTS_STYLE


def test_label_is_bare_number_no_currency_glyph():
    mod = _load_tui()
    assert mod._ritual_pts_label("☀️🎯") == "4"
    assert mod._ritual_pts_label("") == ""
    assert "₦" not in mod._ritual_pts_label("☀️📧🎯⏱️✅")


def test_past_block_header_score_is_red_chip_at_right_edge():
    mod = _load_tui()
    frags = mod._compact_block_lines("辰", 6, [], 42, "6")
    score = _score_fragments(frags, mod)
    assert score and "6" in score[0][1]
    # The 分 points fragment keeps its own style, untouched.
    assert any("42分" in txt for _sty, txt in frags)
    # Right-edge placement: the header line reads `辰:00 ... 6 42分`.
    header = "".join(txt for _sty, txt in frags).split("\n")[0]
    assert header.startswith("辰:00") and header.endswith("6 42分")


def test_no_score_no_chip_fragment():
    mod = _load_tui()
    frags = mod._compact_block_lines("辰", 6, [], 0, "")
    assert not _score_fragments(frags, mod)


def test_mao_line_score_is_red_chip():
    mod = _load_tui()
    mod.STATE.entries = []
    mod.STATE.entries_yday = []
    frags = mod._mao_line("4")
    score = _score_fragments(frags, mod)
    assert score and "4" in score[0][1]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
