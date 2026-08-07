"""Regression (2026-07-21): the habit strip took ~2 minutes to load — the 0n
fetch was ~580 individual per-cell AppleEvents over ssh (per-row date loop to
r500 plus per-cell header/value reads). It must use bulk range reads (~6
AppleEvents, ~1.5s). Same fix pattern as the ص skill's row lookup.

UI follow-up (same day): no ritual emoji chips in the strip — the -1neon
score is the red number on the block lines instead — and the YTD standing
chips come AFTER the daily 0neon value chips."""
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_strip", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_strip"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fetch_src():
    src = (HERE / "janus.py").read_text()
    m = re.search(r"def fetch_habits_today\(\):(.*?)\ndef ", src, re.DOTALL)
    return m.group(1)


def test_fetch_uses_bulk_range_reads_not_per_cell_loops():
    body = _fetch_src()
    assert 'value of range "C3:C500"' in body, "date lookup must be one bulk read"
    assert 'value of range "D1:AS1"' in body, "headers must be one bulk read"
    assert "value of cell c of row" not in body, "no per-cell read loops"
    assert "repeat with r from 3 to 500" not in body, "no per-row date loop"


def test_fetch_unwraps_ranges_via_local_temporary():
    """`item 1 of (value of range ... of ws)` compiles as an element specifier
    dispatched to Excel and errors (-2753 observed live); the unwrap must go
    through a local temporary."""
    body = _fetch_src()
    assert re.search(r"set tmp\w+ to value of range", body)
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("--") and not l.strip().startswith("#"))
    assert "item 1 of (value of range" not in code


def test_strip_has_no_ritual_emoji_chips(monkeypatch):
    """UI decision 2026-07-21: 'numbers with colors [rather] than emojis' —
    the -1neon score is a single red number chip, never emoji chips."""
    mod = _load_tui()
    monkeypatch.setattr(mod, "_read_block_emojis", lambda: {"巳": "7"})
    mod.STATE.day_offset = 0
    mod.STATE.habits_today = [("0l", 1.0), ("hiit", None)]
    mod.STATE.habits_ytd = {"o314": -107.0}
    text = "".join(t for _s, t in mod.render_habits_today())
    for emoji in ("☀️", "📧", "🎯", "⏱️", "✅", "😈"):
        assert emoji not in text


def test_neon_score_chip_leads_done_row(monkeypatch):
    mod = _load_tui()
    monkeypatch.setattr(mod, "_read_block_emojis", lambda: {"巳": "7"})
    monkeypatch.setattr(mod, "hour_to_block", lambda h: ("巳", 8, 9))
    mod.STATE.day_offset = 0
    mod.STATE.habits_today = [("0l", 1.0)]
    mod.STATE.habits_ytd = {}
    frags = mod.render_habits_today()
    sty, txt = frags[0]
    assert txt.strip() == "7" and sty == mod.NEON_PTS_STYLE


def test_ytd_chips_trail_pending_row_in_purples(monkeypatch):
    """User request 2026-07-21 (v3): the YTD standing chips sit on the
    pending line AFTER the pending 0neon names, each in its own purple (the
    dashboard card hues), not standing-red/green. Since 2026-07-24 an
    above-zero standing renders no chip at all, so all three here are ≤ 0.
    2026-08-07: done and pending merged onto one line, done chips first —
    "hiit" (pending) and "o314" (YTD, trails pending) both still land after
    the done chips on that single line."""
    mod = _load_tui()
    monkeypatch.setattr(mod, "_read_block_emojis", lambda: {})
    mod.STATE.day_offset = 0
    mod.STATE.habits_today = [("0l", 1.0), ("hiit", None)]
    mod.STATE.habits_ytd = {"o314": -107.0, "冥想": -80.0, "其他人": 0.0}
    frags = mod.render_habits_today()
    rows = "".join(t for _s, t in frags).rstrip("\n").split("\n")
    assert rows[0].index("hiit") < rows[0].index("o314")
    styles = {t.split()[0]: s for s, t in frags if any(
        n in t for n in mod.HABIT_YTD_COLORS)}
    seen = {styles[n] for n in ("o314", "冥想", "其他人")}
    assert len(seen) == 3, "each YTD chip needs its own purple"
    for n, hexv in mod.HABIT_YTD_COLORS.items():
        assert hexv in styles[n]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
