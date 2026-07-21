"""Regression: the -1₦ block score (₦N) renders in NEON_ACCENT (Radioactive
#c3fc0d), not the dim header style, in every live header path — the compact
block header (past and future-head branches) and the 卯 sleep line (user
request 2026-07-21: "the neon colors in Janus (0n or -1n)... both")."""
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


def _accent_fragments(frags, mod):
    return [(sty, txt) for sty, txt in frags if mod.NEON_ACCENT in sty]


def test_past_block_header_score_is_radioactive():
    mod = _load_tui()
    frags = mod._compact_block_lines("辰", 6, [], 42, "₦6")
    accent = _accent_fragments(frags, mod)
    assert accent and "₦6" in accent[0][1]
    # The 分 points fragment keeps its own style, untouched.
    assert any("42分" in txt for _sty, txt in frags)


def test_no_score_no_accent_fragment():
    mod = _load_tui()
    frags = mod._compact_block_lines("辰", 6, [], 0, "")
    assert not _accent_fragments(frags, mod)


def test_mao_line_score_is_radioactive():
    mod = _load_tui()
    mod.STATE.entries = []
    mod.STATE.entries_yday = []
    frags = mod._mao_line("₦4")
    accent = _accent_fragments(frags, mod)
    assert accent and "₦4" in accent[0][1]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
