"""Daily Dozen line (user request 2026-08-07): a second line under the header
showing this quarter's hcbi "Daily Dozen" category totals as bare-number
chips (DAILY_DOZEN_COLORS) — only categories currently BEHIND (negative;
neutral/positive ones are omitted) — plus a labeled "behind" chip for any
HCBI_BEHIND_DOMAINS total (hcbc/hcbp) that's currently negative — e.g.
"hcbc is currently behind -834分 in q3".

Same turn also: (1) done-row chips no longer always carry a trailing space —
_pack_number_chips drops the gap between differently-colored chips and keeps
it only between same-colored ones; (2) done and pending habit chips share ONE
line, done numbers first, pending names appended right after with no
separator (a follow-up correction: an earlier version of this change put
pending on its own leading line — the user then asked for the two merged
back into one line, done first)."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_daily_dozen", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_daily_dozen"] = mod
    spec.loader.exec_module(mod)
    mod._read_block_emojis = lambda: {}
    mod.STATE.habits_today = []
    mod.STATE.habits_ytd = {}
    mod.STATE.daily_dozen = []
    mod.STATE.hcbi_behind = {}
    return mod


def test_dozen_only_still_renders_even_with_no_habits():
    mod = _load_tui()
    mod.STATE.daily_dozen = [("bn", -32.0)]
    assert mod.render_habits_today() != []


def test_dozen_line_is_the_second_row():
    """done+pending share line 0 (see test_janus_habits_strip.py); the Daily
    Dozen line is line 1."""
    mod = _load_tui()
    mod.STATE.habits_today = [("睡觉", 765.0), ("hiit", None)]
    mod.STATE.daily_dozen = [("bn", -32.0), ("fr", -64.0)]
    text = "".join(t for _, t in mod.render_habits_today())
    lines = [l for l in text.split("\n") if l.strip()]
    assert len(lines) == 2
    assert "-32" in lines[1] and "-64" in lines[1]
    assert "-32" not in lines[0]


def test_dozen_only_shows_categories_currently_behind():
    """Neutral (0) and positive Daily Dozen categories are dropped entirely
    (user request 2026-08-07) — only negative ("behind") ones render."""
    mod = _load_tui()
    mod.STATE.daily_dozen = [("bn", -32.0), ("cr", 0.0), ("g", 36.0)]
    text = "".join(t for _, t in mod.render_habits_today())
    assert "-32" in text
    assert "36" not in text
    # "0" for cr must not appear as a bare dozen chip (a coincidental "0"
    # elsewhere, e.g. inside another number, is not what this guards).
    lines = [l for l in text.split("\n") if l.strip()]
    assert lines[-1] == "-32"


def test_dozen_chip_uses_its_configured_color():
    mod = _load_tui()
    mod.STATE.daily_dozen = [("bn", -32.0)]
    style, text = mod.render_habits_today()[0]
    assert "-32" in text
    assert f"bg:{mod.DAILY_DOZEN_COLORS['bn']}" in style
    assert "#ffffff" in style


def test_dozen_chips_pack_like_the_done_row():
    """Two different-category (hence different-color) dozen chips get no
    gap between them; same convention as the done row's _pack_number_chips."""
    mod = _load_tui()
    mod.STATE.daily_dozen = [("bn", -32.0), ("fr", -64.0)]
    row = "".join(t for _, t in mod.render_habits_today())
    assert "-32-64" in row.replace("\n", "")


def test_behind_chip_shown_only_when_negative():
    mod = _load_tui()
    mod.STATE.daily_dozen = [("bn", -32.0)]
    mod.STATE.hcbi_behind = {"hcbc": -834.0, "hcbp": 243.0}
    text = "".join(t for _, t in mod.render_habits_today())
    assert "hcbc" in text and "-834" in text
    assert "hcbp" not in text and "243" not in text


def test_behind_chip_uses_its_domain_color():
    mod = _load_tui()
    mod.STATE.hcbi_behind = {"hcbc": -834.0}
    frags = mod.render_habits_today()
    style, text = next((s, t) for s, t in frags if "hcbc" in t)
    assert f"bg:{mod.HCBI_BEHIND_DOMAINS['hcbc']}" in style
    assert "-834" in text


def test_no_behind_chips_when_all_positive():
    mod = _load_tui()
    mod.STATE.daily_dozen = [("bn", -32.0)]
    mod.STATE.hcbi_behind = {"hcbc": 10.0, "hcbp": 243.0}
    text = "".join(t for _, t in mod.render_habits_today())
    assert "hcbc" not in text and "hcbp" not in text


# ── fetch_habits_today: hcbi wiring (structural) ────────────────────────────

def test_fetch_reads_hcbi_sheet_for_the_current_quarter():
    src = (HERE / "janus.py").read_text()
    i_def = src.index("def fetch_habits_today():")
    body = src[i_def:src.index("\n\n\n", i_def)]
    assert "_dozen_applescript_lines(q_label)" in body, (
        "must splice in the hcbi Daily Dozen AppleScript, keyed by the "
        "current quarter, not a hardcoded row")
    i_helper = src.index("def _dozen_applescript_lines(")
    helper_body = src[i_helper:src.index("\n\n\n", i_helper)]
    assert 'sheet "hcbi"' in helper_body, "must read the hcbi sheet for the Daily Dozen line"


def test_quarter_label_is_computed_from_the_month_not_hardcoded():
    src = (HERE / "janus.py").read_text()
    assert 'q_label = f"Q{(now.month - 1) // 3 + 1}"' in src


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
