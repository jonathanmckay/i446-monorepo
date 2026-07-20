"""Two-line strip of today's active (nonzero) Neon 0₦ habits, rendered right
below the header (user request 2026-07-20: "use two lines to show the neon
habits for today and render them similar to the way I do in neon itself...
below the title but on the top of janus itself").

render_habits_today() is pure display over STATE.habits_today (populated by
fetch_habits_today, an Excel-over-ssh read like fetch_points) — colored by
domain via the existing project_style()/PROJECT_COLORS machinery rather than
reading Excel's own cell colors. Wraps left-to-right across exactly two
lines; anything past that is dropped (the ask was explicitly two lines, not
a scrolling list)."""
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


def test_renders_name_and_value_for_each_habit():
    mod = _load_tui()
    mod.STATE.habits_today = [("睡觉", 765.0), ("wake up", 1.0)]
    text = "".join(t for _, t in mod.render_habits_today())
    assert "睡觉 765" in text
    assert "wake up 1" in text


def test_integer_values_render_without_a_trailing_point_zero():
    mod = _load_tui()
    mod.STATE.habits_today = [("teams", 3.0)]
    text = "".join(t for _, t in mod.render_habits_today())
    assert "teams 3" in text
    assert "3.0" not in text


def test_mapped_habit_gets_its_domain_color_not_dim():
    mod = _load_tui()
    mod.STATE.habits_today = [("睡觉", 765.0)]
    frags = mod.render_habits_today()
    style = next(s for s, t in frags if "睡觉" in t)
    assert style == mod.project_style("睡觉")
    assert style != "class:dim"


def test_unmapped_habit_falls_back_to_dim_not_a_crash():
    mod = _load_tui()
    mod.STATE.habits_today = [("some totally new column", 1.0)]
    frags = mod.render_habits_today()
    style = next(s for s, t in frags if "some totally new column" in t)
    assert style == "class:dim"


def test_output_never_exceeds_two_lines():
    mod = _load_tui()
    # Many habits -- guaranteed to overflow two WIDTH_HINT-wide lines.
    mod.STATE.habits_today = [(f"habit{i}", float(i + 1)) for i in range(40)]
    text = "".join(t for _, t in mod.render_habits_today())
    assert text.count("\n") <= 2


def test_wraps_to_second_line_when_first_is_full():
    mod = _load_tui()
    # Long names guarantee the first line fills before all habits fit.
    names = [f"a very long habit name number {i}" for i in range(6)]
    mod.STATE.habits_today = [(n, 1.0) for n in names]
    text = "".join(t for _, t in mod.render_habits_today())
    lines = [l for l in text.split("\n") if l.strip()]
    assert len(lines) == 2
    assert lines[0] != lines[1]


def test_fetch_habits_today_skips_internal_bookkeeping_columns():
    """Structural: 'N color'/'⎣∀clr'/'#' etc. are internal sheet-bookkeeping
    columns, not real habits -- fetch_habits_today must not surface them."""
    src = (HERE / "janus.py").read_text()
    i_def = src.index("def fetch_habits_today():")
    body = src[i_def:src.index("\n\n\n", i_def)]
    assert "_HABIT_STRIP_SKIP" in body


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
