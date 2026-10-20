"""Daily Dozen line (user request 2026-08-07): a second line under the header
showing this quarter's hcbi "Daily Dozen" category totals as bare-number
chips (DAILY_DOZEN_COLORS) — only categories currently BEHIND (negative;
neutral/positive ones are omitted) — plus a labeled "behind" chip for any
HCBI_BEHIND_DOMAINS total (hcb/hcbp) that's currently negative — e.g.
"hcb is currently behind -834分 in q3".

2026-08-12: "hcb" was renamed from "hcbc" and switched from the hcbi
label row's column X (food-only subtotal) to that same column's SECOND
row (=SUM(X<label>:Y<label>), the combined hcbc+hcbp total) — the
persistent header chip should reflect overall hcb standing, not just
food. See test_hcb_behind_reads_column_x_second_row below.

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
    text = "".join(t for _, t, *_ in mod.render_habits_today())
    lines = [l for l in text.split("\n") if l.strip()]
    assert len(lines) == 2
    assert "-32" in lines[1] and "-64" in lines[1]
    assert "-32" not in lines[0]


def test_dozen_only_shows_categories_currently_behind():
    """Neutral (0) and positive Daily Dozen categories are dropped entirely
    (user request 2026-08-07) — only negative ("behind") ones render."""
    mod = _load_tui()
    mod.STATE.daily_dozen = [("bn", -32.0), ("cr", 0.0), ("g", 36.0)]
    text = "".join(t for _, t, *_ in mod.render_habits_today())
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
    row = "".join(t for _, t, *_ in mod.render_habits_today())
    assert "-32-64" in row.replace("\n", "")


def test_behind_chip_shown_only_when_negative():
    mod = _load_tui()
    mod.STATE.daily_dozen = [("bn", -32.0)]
    mod.STATE.hcbi_behind = {"hcb": -834.0, "hcbp": 243.0}
    text = "".join(t for _, t, *_ in mod.render_habits_today())
    assert "hcb" in text and "-834" in text
    assert "hcbp" not in text and "243" not in text


def test_behind_chip_uses_its_domain_color():
    mod = _load_tui()
    mod.STATE.hcbi_behind = {"hcb": -834.0}
    frags = mod.render_habits_today()
    style, text = next((s, t) for s, t, *_ in frags if "hcb" in t)
    assert f"bg:{mod.HCBI_BEHIND_DOMAINS['hcb']}" in style
    assert "-834" in text


def test_hcbp_chip_hidden_when_positive_but_hcb_overall_stays_up():
    """Bug 2026-10-01 ("janus still not showing overall hcb points"): hcb is
    the OVERALL standing (the 2026-08-12 switch to the combined total made
    it the persistent header chip), so it must render whatever its sign.
    hcbp keeps the negative-only "behind" rule."""
    mod = _load_tui()
    mod.STATE.daily_dozen = [("bn", -32.0)]
    mod.STATE.hcbi_behind = {"hcb": 264.0, "hcbp": 1237.0}
    text = "".join(t for _, t, *_ in mod.render_habits_today())
    assert "hcb +264" in text, text
    assert "hcbp" not in text and "1237" not in text


def test_hcb_chip_absent_only_when_no_value_was_read():
    mod = _load_tui()
    mod.STATE.daily_dozen = [("bn", -32.0)]
    mod.STATE.hcbi_behind = {"hcbp": 243.0}
    text = "".join(t for _, t, *_ in mod.render_habits_today())
    assert "hcb" not in text


# ── fetch_habits_today: hcbi wiring (structural) ────────────────────────────

def test_fetch_reads_hcbi_sheet_for_the_current_quarter():
    src = (HERE / "janus.py").read_text()
    i_def = src.index("def fetch_habits_today():")
    body = src[i_def:src.index("\n\n\n", i_def)]
    assert "_dozen_applescript_lines(q_label, prev_q_label)" in body, (
        "must splice in the hcbi Daily Dozen AppleScript, keyed by the "
        "current quarter (and the previous one, for the running hcb total), "
        "not a hardcoded row")
    i_helper = src.index("def _dozen_applescript_lines(")
    helper_body = src[i_helper:src.index("\n\n\n", i_helper)]
    assert 'sheet "hcbi"' in helper_body, "must read the hcbi sheet for the Daily Dozen line"


def test_quarter_label_is_computed_from_the_month_not_hardcoded():
    src = (HERE / "janus.py").read_text()
    assert 'q_label = f"Q{(now.month - 1) // 3 + 1}"' in src


class _Proc:
    def __init__(self, stdout):
        self.returncode = 0
        self.stdout = stdout


def test_hcb_reads_the_sheets_own_combined_cell_x388(monkeypatch):
    """Per JM 2026-10-01 ("shouldn't you be using X388"): the overall hcb
    number is hcbi!X388, the sheet's own combined cell (=SUM(X378,X375),
    "Q2+Q3" today; JM moves the window by editing that formula) — the same
    cell the jm dashboard's hcbp+hcbc card mirrors. janus must read it
    verbatim, never re-derive a quarter sum from the row pair (that gave
    +264 while the sheet said -928). hcbp (column Y) is unaffected."""
    mod = _load_tui()
    row1 = [""] * 21
    row1[19] = "-840.9"   # label-row X (food subtotal): must NOT feed hcb
    row1[20] = "243.0"    # label-row Y (hcbp)
    row2 = [""] * 21
    row2[19] = "-439.9"   # row-below X (combined SUM): must NOT feed hcb either
    dozen_raw = ",".join(row1) + "|" + ",".join(row2) + "#-928"
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc("||" + "||" + dozen_raw))
    mod.fetch_habits_today()
    assert mod.STATE.hcbi_behind.get("hcb") == -928.0
    assert mod.STATE.hcbi_behind.get("hcbp") == 243.0


def test_hcb_absent_when_x388_was_not_read(monkeypatch):
    """No "#" total (the X388 read failed) must drop the hcb chip, not fall
    back to a quarter-derived number the sheet doesn't show."""
    mod = _load_tui()
    row1 = [""] * 21
    row2 = [""] * 21
    row2[19] = "-439.9"
    dozen_raw = ",".join(row1) + "|" + ",".join(row2)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc("||" + "||" + dozen_raw))
    mod.fetch_habits_today()
    assert "hcb" not in mod.STATE.hcbi_behind


def test_hcb_total_survives_a_prev_quarter_block(monkeypatch):
    """Segment shape <cur>;<prev>#<total>: the ';' prev block (still needed
    for the Daily Dozen and hcbp running totals) must not swallow the
    '#' total."""
    mod = _load_tui()
    row1 = [""] * 21
    row2 = [""] * 21
    row2[19] = "191.9"
    dozen_raw = ",".join(row1) + "|" + ",".join(row2) + ";" + ",".join(row1) + "|" + ",".join(row2) + "#-928"
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc("||" + "||" + dozen_raw))
    mod.fetch_habits_today()
    assert mod.STATE.hcbi_behind.get("hcb") == -928.0


def test_hcb_applescript_reads_x388():
    src = (HERE / "janus.py").read_text()
    assert 'HCBI_HCB_TOTAL_CELL = "X388"' in src
    i = src.index("def _dozen_applescript_lines(")
    body = src[i:src.index("\ndef ", i + 1)]
    assert 'range "{HCBI_HCB_TOTAL_CELL}"' in body and '"#"' in body, \
        "the dozen AppleScript must append '#' + hcbi!X388"


def test_prev_quarter_label_skipped_for_q1():
    """No 'Q0' exists on the sheet -- Q1 must pass an empty prev_q_label so
    _dozen_applescript_lines' prev_block is a no-op, not a doomed search for
    a row that will never match."""
    mod = _load_tui()
    src = (HERE / "janus.py").read_text()
    assert 'prev_q_label = f"Q{_q_num - 1}" if _q_num > 1 else ""' in src


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


# ── quarter boundary: running Q<n-1>+Q<n> for the dozen, hcb AND hcbp ───────
# (bug 2026-10-01: on day one of Q4 the dozen read all-zero so no category
# rendered, and hcbp showed Q4's lone -86 instead of the running 1323-86)

# E..Y (21 cells) of the hcbi Q3 and Q4 row pairs, exactly as read 2026-10-01,
# built from column letters so the fixtures can't drift from the sheet layout.
_COLS = "EFGHIJKLMNOPQRSTUVWXY"


def _row(**cells):
    return ",".join(str(cells.get(c, "")) for c in _COLS) + ","


def _pair(row1, row2):
    return _row(**row1) + "|" + _row(**row2)


Q3_ROWS = _pair(dict(E=-87, G=-175, I=-39, K=5, M=2, O=3, Q=-149, X=-585, Y=1323),
                dict(F=2, H=6, J=-15, L=136, N=13, X=738))
Q4_ROWS = _pair(dict(E=0, G=0, I=0, K=0, M=0, O=0, Q=0, X=-494, Y=-86),
                dict(F=0, H=0, J=0, L=0, N=0, X=0))


def test_parse_dozen_segment_sums_previous_quarter_into_current():
    m = _load_tui()
    dozen, behind = m._parse_dozen_segment(Q4_ROWS, Q3_ROWS)
    assert dict(dozen)["bn"] == -87 and dict(dozen)["fr"] == -175 and dict(dozen)["vg"] == -15
    assert dict(dozen)["g"] == 136 and dict(dozen)["br"] == 2
    assert "hcb" not in behind           # never derived from the quarter rows
    assert behind["hcbp"] == 1323 - 86   # Q4 Y + Q3 Y, not Q4 alone
    _, behind = m._parse_dozen_segment(Q4_ROWS, Q3_ROWS, "-928")
    assert behind["hcb"] == -928         # hcbi!X388 verbatim


def test_parse_dozen_segment_q4_alone_would_have_hidden_everything():
    m = _load_tui()
    dozen, behind = m._parse_dozen_segment(Q4_ROWS, "")
    assert all(v == 0 for _, v in dozen)
    assert behind["hcbp"] == -86 and "hcb" not in behind


def test_parse_dozen_segment_q1_has_no_previous_quarter():
    m = _load_tui()
    dozen, behind = m._parse_dozen_segment(Q3_ROWS, "")
    assert dict(dozen)["bn"] == -87 and behind["hcbp"] == 1323 and "hcb" not in behind


def test_parse_dozen_segment_tolerates_legacy_bare_prev_x():
    m = _load_tui()
    _, behind = m._parse_dozen_segment(Q3_ROWS, "500")
    assert "hcb" not in behind and behind["hcbp"] == 1323


def test_prev_quarter_applescript_reads_the_full_row_pair():
    src = (HERE / "janus.py").read_text()
    i = src.index("def _dozen_applescript_lines(")
    body = src[i:src.index("\ndef ", i + 1)]
    prev = body[body.index("prev_block = f'''"):body.index("return f'''")]
    assert '("E" & pRow & ":Y" & (pRow + 1))' in prev, "prev quarter must return E:Y of both rows"
    assert '("X" & (pRow + 1))' not in prev, "the old single-cell X read must be gone"
