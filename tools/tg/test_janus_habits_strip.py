"""Two-row strip of today's Neon 0₦ habits, rendered right below the header
(user request 2026-07-20: "use two lines to show the neon habits for
today... below the title but on the top of janus itself").

render_habits_today() is pure display over STATE.habits_today (populated by
fetch_habits_today, an Excel-over-ssh read like fetch_points, which now
carries EVERY habit — done and not — not just nonzero ones).

Two follow-ups landed the same day, both reflected here:
1. No name label on done habits — "takes up too much space... make the
   color the background color... usually the text will be white" — a done
   habit renders as a bare value inside a solid-background chip (color =
   domain identity, via PROJECT_COLORS/_habit_chip_style, not Excel's own
   cell colors).
2. Split by DONE-ness rather than wrapped across two lines of the same
   thing — "everything done gets the numbers and then 1 space (not 2)...
   the second row is the things yet to be done, and that is just the row
   names." Each row independently drops whatever doesn't fit in WIDTH_HINT
   (the ask was two lines, not a scrolling list)."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_habits_strip", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_habits_strip"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_empty_habits_renders_nothing():
    mod = _load_tui()
    mod.STATE.habits_today = []
    assert mod.render_habits_today() == []


def test_done_row_is_bare_value_no_name_label():
    mod = _load_tui()
    mod.STATE.habits_today = [("睡觉", 765.0), ("wake up", 1.0)]
    frags = mod.render_habits_today()
    row1 = "".join(t for _, t in frags).split("\n")[0]
    assert "765" in row1 and "1" in row1
    assert "睡觉" not in row1 and "wake up" not in row1


def test_pending_row_is_the_bare_habit_name():
    mod = _load_tui()
    mod.STATE.habits_today = [("hiit", None), ("teams", None)]
    text = "".join(t for _, t in mod.render_habits_today())
    lines = [l for l in text.split("\n") if l.strip()]
    assert len(lines) == 1  # nothing done yet -> only the pending row renders
    assert "hiit" in lines[0] and "teams" in lines[0]


def test_done_and_pending_split_into_separate_rows():
    mod = _load_tui()
    mod.STATE.habits_today = [("睡觉", 765.0), ("hiit", None)]
    text = "".join(t for _, t in mod.render_habits_today())
    lines = [l for l in text.split("\n") if l.strip()]
    assert len(lines) == 2
    assert "765" in lines[0] and "睡觉" not in lines[0]
    assert "hiit" in lines[1] and "765" not in lines[1]


def test_done_chips_use_single_trailing_space_not_two():
    """Follow-up (2026-07-20): "1 space (not 2 like right now)" between
    done-row chips."""
    mod = _load_tui()
    mod.STATE.habits_today = [("teams", 3.0), ("push", 4.0)]
    row1 = "".join(t for _, t in mod.render_habits_today()).split("\n")[0]
    assert "3 4" in row1, f"exactly one space must separate chips: {row1!r}"


def test_integer_values_render_without_a_trailing_point_zero():
    mod = _load_tui()
    mod.STATE.habits_today = [("teams", 3.0)]
    row1 = "".join(t for _, t in mod.render_habits_today()).split("\n")[0]
    assert "3" in row1
    assert "3.0" not in row1


def test_done_chip_gets_its_domain_background_color():
    mod = _load_tui()
    mod.STATE.habits_today = [("睡觉", 765.0)]
    style, text = mod.render_habits_today()[0]
    assert "765" in text
    assert style == mod._habit_chip_style("睡觉")
    assert f"bg:{mod.PROJECT_COLORS['睡觉']}" in style
    assert "#ffffff" in style, "chip text must default to white"


def test_pending_chip_also_gets_its_domain_background_color():
    mod = _load_tui()
    mod.STATE.habits_today = [("hiit", None)]
    style, text = mod.render_habits_today()[0]
    assert "hiit" in text
    # _habit_chip_style takes the habit NAME since 2026-07-21 (workbook row-2
    # colors win; the domain map is the fallback exercised here).
    assert style == mod._habit_chip_style("hiit")
    assert f"bg:{mod.PROJECT_COLORS[mod.HABIT_COLOR_DOMAIN['hiit']]}" in style


def test_workbook_row2_color_beats_domain_fallback():
    """The 0n sheet's row-2 cell fill is the canonical neon color; when the
    fetch has populated STATE.habit_colors it must win over the domain map,
    and a light fill must flip the chip text to black for contrast."""
    mod = _load_tui()
    mod.STATE.habit_colors = {"0t": "#0d3b66", "早餐": "#feccff"}
    mod.STATE.habits_today = [("0t", None), ("早餐", None)]
    (s_0t, _), (s_zc, _) = mod.render_habits_today()[:2]
    assert "bg:#0d3b66" in s_0t and "#ffffff" in s_0t
    assert "bg:#feccff" in s_zc and "#000000" in s_zc


def test_unmapped_habit_still_gets_a_visible_chip_not_a_crash():
    """A value with no background at all would read as plain text, breaking
    the "everything here is a colored chip" scan -- an unmapped habit still
    gets a neutral chip rather than no styling."""
    mod = _load_tui()
    mod.STATE.habits_today = [("some totally new column", 1.0)]
    style, text = mod.render_habits_today()[0]
    assert "1" in text
    assert "bg:" in style and "#ffffff" in style


def test_output_never_exceeds_two_lines():
    mod = _load_tui()
    # Many habits, half done half pending -- guaranteed to overflow WIDTH_HINT.
    mod.STATE.habits_today = [(f"habit{i}", float(i % 2)) for i in range(40)]
    text = "".join(t for _, t in mod.render_habits_today())
    assert text.count("\n") <= 2


def test_each_row_drops_overflow_independently():
    mod = _load_tui()
    mod.STATE.habits_today = [(f"habit{i}", float(i + 1)) for i in range(mod.WIDTH_HINT)]
    text = "".join(t for _, t in mod.render_habits_today())
    lines = [l for l in text.split("\n") if l.strip()]
    assert len(lines) == 1  # all "done" (nonzero) -> only row 1 renders
    assert len(lines[0]) <= mod.WIDTH_HINT + 1


def test_fetch_habits_today_keeps_zero_value_habits_too():
    """Structural: fetch_habits_today must no longer filter to nonzero-only
    -- the pending row needs the not-yet-done habits too."""
    src = (HERE / "janus.py").read_text()
    i_def = src.index("def fetch_habits_today():")
    body = src[i_def:src.index("\n\n\n", i_def)]
    assert "if v:" not in body, "must not still filter out zero-value habits"


def test_fetch_habits_today_skips_internal_bookkeeping_columns():
    """Structural: 'N color'/'⎣∀clr'/'#' etc. are internal sheet-bookkeeping
    columns, not real habits -- fetch_habits_today must not surface them."""
    src = (HERE / "janus.py").read_text()
    i_def = src.index("def fetch_habits_today():")
    body = src[i_def:src.index("\n\n\n", i_def)]
    assert "_HABIT_STRIP_SKIP" in body


def test_user_requested_exclusions_are_skipped():
    """User requests (2026-07-20): "remove 词汇"/"remove github [slack
    github]"/"问学 is also optional" from what this strip tracks -- still
    tracked in Neon, just not wanted here."""
    mod = _load_tui()
    assert "词汇" in mod._HABIT_STRIP_SKIP
    assert "slack github" in mod._HABIT_STRIP_SKIP
    assert "问学" in mod._HABIT_STRIP_SKIP


class _FakeProc:
    def __init__(self, out):
        self.returncode = 0
        self.stdout = out
        self.stderr = ""


def _fetch(monkeypatch, mod, chunks):
    """chunks: list of (habit_name, raw_value_string) pairs, exactly what
    the ix-osa AppleScript would emit as "name\\tvalue|name\\tvalue|..."."""
    out = "|".join(f"{name}\t{val}" for name, val in chunks)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _FakeProc(out))
    mod.fetch_habits_today()


def test_explicit_zero_is_excluded_entirely_not_shown_as_pending_or_done(monkeypatch):
    """Regression (user report 2026-07-20): "if CPAP is marked zero in neon
    (not blank) it shouldn't show up" -- distinct from a genuinely blank
    (not-yet-done) cell, which DOES belong on the pending row."""
    mod = _load_tui()
    _fetch(monkeypatch, mod, [("cpap", "0")])
    assert mod.STATE.habits_today == [], "an explicit 0 must not appear on EITHER row"


def test_blank_cell_is_pending_not_excluded(monkeypatch):
    mod = _load_tui()
    _fetch(monkeypatch, mod, [("cpap", "")])
    assert mod.STATE.habits_today == [("cpap", None)]


def test_nonzero_value_is_done(monkeypatch):
    mod = _load_tui()
    _fetch(monkeypatch, mod, [("teams", "3")])
    assert mod.STATE.habits_today == [("teams", 3.0)]


def test_render_all_places_habit_strip_right_after_header():
    src = (HERE / "janus.py").read_text()
    i_def = src.index("def render_all()")
    body = src[i_def:src.index("\n\n\n", i_def)]
    header_i = body.index("render_header()")
    habits_i = body.index("render_habits_today()")
    morning_i = body.index("render_morning()")
    assert header_i < habits_i < morning_i


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
