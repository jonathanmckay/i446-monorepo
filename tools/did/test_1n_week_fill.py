"""JM 2026-11-05: a weekly card fills a blank last week before this one."""
import ast
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))
from neon import allcolors as ac  # noqa: E402
from neon import week_fill as wf  # noqa: E402
from neon import weeks  # noqa: E402

import pytest  # noqa: E402

THU = date(2026, 11, 5)   # week 11.1; last week 10.4
T, E = "T", "E"           # card column; rate-card column


def sheet(prev="", cur="", first=False):
    # B5:… → B=label, C.., E is index 3, T is index 18.
    def row(label, t, e=""):
        r = [""] * 37
        r[0], r[3], r[18] = label, e, t
        return r
    head = [""] * 37
    head[3], head[18] = "1/m", "15"
    labels = ("12.5", "1.1") if first else ("10.4", "11.1")
    return wf.Sheet([head, row(labels[0], prev), row(labels[1], cur)])


@pytest.fixture(autouse=True)
def memo(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "MEMO", tmp_path / "memo.json")


def test_blank_last_week_takes_the_completion():
    t = wf.resolve("1 hcmc", T, THU, sheet(), now=1000)
    assert (t.label, t.row, t.open, t.shifted) == ("10.4", 6, True, True)


def test_filled_or_na_last_week_keeps_this_week():
    for prev in ("1.0", "0.0"):
        t = wf.resolve("1 hcmc", T, THU, sheet(prev=prev), now=1000)
        assert (t.label, t.shifted) == ("11.1", False)


def test_exempt_and_rate_cards_keep_the_natural_week():
    assert wf.resolve("nails", T, THU, sheet()) is None
    assert wf.resolve("1 Kids  Nature ", T, THU, sheet()) is None
    assert wf.resolve("family", E, THU, sheet()) is None


def test_week_one_does_not_reach_last_year():
    jan = date(2027, 1, 7)
    assert weeks.fiscal_week_label(jan) == "1.1"
    assert wf.resolve("1 hcmc", T, jan, sheet(first=True), now=1000).label == "1.1"


def test_retry_after_the_write_landed_hits_the_same_cell():
    first = wf.resolve("1 hcmc", T, THU, sheet(), now=1000)
    retry = wf.resolve("1 hcmc", T, THU, sheet(prev="1.0"), now=1100)
    assert retry.label == first.label and not retry.open


def test_second_completion_later_lands_on_this_week():
    wf.resolve("1 hcmc", T, THU, sheet(), now=1000)
    t = wf.resolve("1 hcmc", T, THU, sheet(prev="1.0"), now=1000 + wf.MEMO_TTL + 1)
    assert (t.label, t.open, t.shifted) == ("11.1", True, False)


def test_read_failure_keeps_the_natural_week(monkeypatch):
    monkeypatch.setattr(wf, "load", lambda: None)
    assert wf.resolve("1 hcmc", T, THU) is None


def test_monday_cycle_cards_use_their_own_previous_cycle():
    assert weeks.habit_week_label("1 -2g", date(2026, 10, 25)) == "10.3"
    assert weeks.prev_habit_week_label("1 -2g", date(2026, 10, 25)) == "10.2"
    assert weeks.prev_habit_week_label("1 hcmc", THU) == "10.4"


def test_allcolors_counts_a_catch_up_done_this_cycle():
    blank = ac.Cell("1 hcmc", ac.BLANK)
    sun, at = date(2026, 11, 1), datetime(2026, 11, 3, 9, 0)
    assert ac.caught_up(blank, "1.0", at, sun, THU).status == ac.DONE
    assert ac.caught_up(blank, "1.0", datetime(2026, 10, 30, 9), sun, THU).status == ac.BLANK
    assert ac.caught_up(blank, "1.0", datetime(2026, 11, 6, 9), sun, THU).status == ac.BLANK
    assert ac.caught_up(blank, "", at, sun, THU).status == ac.BLANK


def _src(name):
    return (HERE / name).read_text()


def test_both_writers_use_the_resolver_and_skip_past_dates():
    run = _src("run.py")
    assert "week_fill.resolve(name, col, _target_date_obj(target_date))" in run
    assert 'target_date == _today_md() and not d.get("cumulative_increment")' in run
    fast = _src("did-fast.py")
    assert "None if past_target else neon_week_fill.load()" in fast
    ast.parse(fast)


def test_did_fast_keeps_row_and_pre_image_per_group():
    fast = _src("did-fast.py")
    assert 'week_row_by_col.get(r.col_letter, week_row)' in fast
    assert "pre_1n.update(parse_pre_lines(_group_result.stdout))" in fast
    assert "one_n_result = parse" not in fast


def test_survey_note_fills_blank_last_week():
    import importlib.util
    p = HERE.parent / "hcmc" / "hcmc-survey.py"
    spec = importlib.util.spec_from_file_location("hcmc_survey", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    both = {"PREV": (44, ""), "CUR": (45, "")}
    assert m.target(both, "11.1", "10.4") == ("10.4", 44, "")
    assert m.target({"PREV": (44, "x"), "CUR": (45, "")}, "11.1", "10.4") == ("11.1", 45, "")
    assert m.target(both, "1.1", "12.5") == ("1.1", 45, "")
