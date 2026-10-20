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


def test_expand_selections_leaves_prose_alone():
    m = _load()
    ctx = {"titles": [(0, "A")]}
    out = m.expand_selections({"title": "Big week 3"}, ctx)
    assert out["title"] == "Big week 3"   # prose untouched


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


def test_no_formula_fields_in_form():
    """Regression (2026-07-21): Rating (P), High (W), Low (X), Avg (Y) hold
    live sheet formulas (P='i9'!B{row}; W/X/Y aggregate the week's 0s897
    ⌈/⌊/x̄) — they compute themselves from the daily surveys. A form write
    would clobber the formulas, so those columns must never be FIELDS."""
    m = _load()
    cols = {c for _k, _l, c, _kind, _ck in m.FIELDS}
    assert not cols & {"P", "W", "X", "Y"}


def test_applescript_targets_week_row_and_skips_blanks():
    m = _load()
    script = m.build_applescript({"title": "T", "win": "", "notes": "n"},
                                 dt.date(2026, 7, 12))
    assert '"7.2"' in script
    assert '("R" & weekRow)' in script          # title col
    assert '("AO" & weekRow)' in script         # notes col
    assert '("S" & weekRow)' not in script      # blank win skipped
    assert "save wb" in script


def test_check_week_flags_missing_days(monkeypatch):
    m = _load()
    dates = [dt.date(2026, 7, 12) + dt.timedelta(days=i) for i in range(7)]
    ctx = {"titles": [(0, "t"), (1, "t"), (3, "t"), (4, "t"), (5, "t"), (6, "t")]}
    # 0l done every day except day 2; tracking fine except day 6.
    raw_0l = "".join("<<D>>%d<<F>>1.0" % i for i in range(7) if i != 2)
    monkeypatch.setattr(m.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": raw_0l, "stderr": ""})())
    monkeypatch.setattr(m, "_toggl_minutes_by_day",
                        lambda ds: {d: (600 if i == 6 else 1400) for i, d in enumerate(ds)})
    b = m.check_week(dates, ctx)
    assert b["missing_0s"] == [dates[2]]
    assert b["missing_0l"] == [dates[2]]
    assert b["low_tracking"] == [(dates[6], 600)]
    text = m.format_blockers(b)
    assert "/0s 2026-07-14" in text and "/did 0l 7/14" in text and "/tg" in text


def test_check_week_clear_when_complete(monkeypatch):
    m = _load()
    dates = [dt.date(2026, 7, 12) + dt.timedelta(days=i) for i in range(7)]
    ctx = {"titles": [(i, "t") for i in range(7)]}
    raw_0l = "".join("<<D>>%d<<F>>1.0" % i for i in range(7))
    monkeypatch.setattr(m.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": raw_0l, "stderr": ""})())
    monkeypatch.setattr(m, "_toggl_minutes_by_day",
                        lambda ds: {d: 1400 for d in ds})
    b = m.check_week(dates, ctx)
    assert not any(b.values())


def test_survey_save_marks_1s_done_in_source():
    """User decision 2026-07-21: completing the survey completes the weekly
    1s task — the tool runs did-fast's runner after a successful write (with
    a --no-mark escape hatch), and the /1s skill no longer marks it."""
    src = (HERE / "1s-survey.py").read_text()
    assert 'run.py"), "1s"' in src.replace("'", '"')
    assert "--no-mark" in src


def test_daily_context_uses_string_value_for_dates():
    """Col B holds real date cells: `value` yields date objects whose text is
    the long form, silently matching nothing (bug found live 2026-07-21).
    The context fetch must read the DISPLAYED string."""
    m = _load()
    script = m.build_context_script([dt.date(2026, 7, 12)])
    assert 'string value of range "B3:B600"' in script


# ── Shared BackgroundWriter adoption (2026-08-11) ───────────────────────────
# 1s had NO recovery mechanism before this, same gap 0s had. Adopting
# lib/review_form_writer.py's BackgroundWriter (the same module xk887/0s use)
# backfills that. No wall-clock speedup here either (single-page form, no
# "next page" to advance into) — the Neon write and the 1s-mark-done call
# stay serialized on purpose (JM 2026-08-11), same reasoning as 0s.

def test_main_uses_shared_background_writer():
    m = _load()
    assert isinstance(m._writer, m.BackgroundWriter)


def test_drain_happens_before_cmux_close_in_source():
    """close-surface tears down the pane; it must never fire while a queued
    write might still need the terminal to report its result."""
    import inspect
    m = _load()
    src = inspect.getsource(m.main)
    drain_idx = src.index("_writer.drain()")
    close_idx = src.index('"cmux", "close-surface"')
    assert drain_idx < close_idx, "drain() must run before cmux close-surface"


def test_write_failure_dumps_recovery_and_returns_nonzero(monkeypatch, tmp_path):
    m = _load()
    monkeypatch.setattr(m, "RECOVERY_DIR", tmp_path)
    monkeypatch.setattr(m, "fetch_context", lambda dates: {k: [] for k in m.DAILY_COLS})

    def _boom(answers, sunday):
        raise RuntimeError("Invalid object specifier (workbook not open)")
    monkeypatch.setattr(m, "write_answers", _boom)

    f = tmp_path / "answers.json"
    f.write_text('{"good": "typed answer"}')
    monkeypatch.setattr("sys.argv", ["1s-survey.py", "--from-json", str(f), "--no-mark"])

    rc = m.main()
    assert rc == 1, "a failed Neon write must make main() exit non-zero"
    assert list(tmp_path.glob("recovery*.json")), "expected a recovery dump for the failed write"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
