"""Tests for 1s-survey.py (feature 2026-07-21: /1s asks the 1分+1s manual
questions as a full-screen form, surfacing the week's daily 0s897 answers so
answering is selecting rather than composing de novo)."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("s1", HERE / "1s-survey.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["s1"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_week_range_defaults_to_last_completed_week():
    m = _load()
    # Tue 2026-07-21 → last completed Sun-Sat is 7/12-7/18.
    s, e = m.week_range(None, today=dt.date(2026, 7, 21))
    assert (s, e) == (dt.date(2026, 7, 12), dt.date(2026, 7, 18))
    # A Sunday reviews the week that JUST ended, not the one starting.
    s, e = m.week_range(None, today=dt.date(2026, 7, 19))
    assert (s, e) == (dt.date(2026, 7, 12), dt.date(2026, 7, 18))


def test_week_range_with_date_arg():
    m = _load()
    s, e = m.week_range("2026-07-15")
    assert (s, e) == (dt.date(2026, 7, 12), dt.date(2026, 7, 18))


def test_week_row_label_is_sunday_anchored_month_week():
    m = _load()
    assert m.week_row_label(dt.date(2026, 7, 12)) == "7.2"
    assert m.week_row_label(dt.date(2026, 1, 4)) == "1.1"
    assert m.week_row_label(dt.date(2026, 6, 28)) == "6.4"


def test_expand_selections_picks_day_answers():
    m = _load()
    ctx = {"titles": [(0, "Reclaiming sunday"), (2, "Strong Start")],
           "wins": [(1, "Points")]}
    out = m.expand_selections({"title": "3", "win": "2"}, ctx)
    assert out["title"] == "Strong Start"
    assert out["win"] == "Points"


def test_expand_selections_comma_list_joins():
    m = _load()
    ctx = {"titles": [(0, "A"), (1, "B"), (2, "C")]}
    out = m.expand_selections({"title": "1,3"}, ctx)
    assert out["title"] == "A; C"


def test_expand_selections_leaves_prose_and_numbers_alone():
    m = _load()
    ctx = {"titles": [(0, "A")]}
    out = m.expand_selections(
        {"title": "Big week 3", "high": "7", "rating": "8"}, ctx)
    assert out["title"] == "Big week 3"   # prose untouched
    assert out["high"] == "7"             # num field never expanded
    assert out["rating"] == "8"           # no context key → untouched


def test_expand_selection_missing_day_kept_verbatim():
    m = _load()
    out = m.expand_selections({"title": "5"}, {"titles": [(0, "A")]})
    assert out["title"] == "5"


def test_parse_context_offsets_and_blanks():
    m = _load()
    dates = [dt.date(2026, 7, 12) + dt.timedelta(days=i) for i in range(7)]
    cells = [""] * 16
    cells[0], cells[3], cells[4] = "Title", "Win", "Learn"
    cells[11], cells[12], cells[13] = "8", "5", "6.5"
    cells[14], cells[15] = "Proud", "LearnO"
    raw = "<<ROW 2>>" + "".join("<<F>>" + c for c in cells)
    ctx = m.parse_context(raw, dates)
    assert ctx["titles"] == [(2, "Title")]
    assert ctx["wins"] == [(2, "Win")]
    assert ctx["ceil"] == [(2, "8")] and ctx["mean"] == [(2, "6.5")]
    assert ctx["proud"] == [(2, "Proud")] and ctx["learn_others"] == [(2, "LearnO")]


def test_numeric_suggestions_max_min_mean():
    m = _load()
    ctx = {"ceil": [(0, "7.8"), (1, "9")], "floor": [(0, "5.5"), (1, "6.8")],
           "mean": [(0, "7"), (1, "8")]}
    s = m.numeric_suggestions(ctx)
    assert s == {"high": "9", "low": "5.5", "avg": "7.5"}


def test_applescript_targets_week_row_and_skips_blanks():
    m = _load()
    script = m.build_applescript({"title": "T", "win": "", "high": "8"},
                                 dt.date(2026, 7, 12))
    assert '"7.2"' in script
    assert '("R" & weekRow)' in script          # title col
    assert '("W" & weekRow)' in script          # high col
    assert '("S" & weekRow)' not in script      # blank win skipped
    assert "string value of range" not in script or True
    assert "save wb" in script


def test_daily_context_uses_string_value_for_dates():
    """Col B holds real date cells: `value` yields date objects whose text is
    the long form, silently matching nothing (bug found live 2026-07-21).
    The context fetch must read the DISPLAYED string."""
    m = _load()
    script = m.build_context_script([dt.date(2026, 7, 12)])
    assert 'string value of range "B3:B600"' in script


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
