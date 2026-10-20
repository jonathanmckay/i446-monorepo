"""
Regression test for /1s897 week selection.

Bug (2026-04-22): When run on a Wednesday, the skill wrote the social review
to next week's row (Wed 4/22 - Tue 4/28) instead of the just-completed week
(Wed 4/15 - Tue 4/21). Root cause was ambiguous wording in Step 1 of SKILL.md
that let the model treat "today" as the start of the target week on Wednesdays.

This test pins the day-of-week -> (week_start, week_end) mapping the skill is
required to follow, and asserts the SKILL.md text contains the corrected,
unambiguous rules.

Feature (2026-08-13): "does 1s897 take in my m.w notation" -- it didn't; it
only ever targeted the most recent complete week. week_calc.py adds an
optional arg: an 'M.W' label or a plain ISO date, both resolved to the
Wed-Tue week that CONTAINS the corresponding date -- mirroring
xk887-survey.py's week_range(), which already does this for its own Sun-Sat
weeks. These tests cover the new arg-parsing path; the ones above cover the
pre-existing no-arg default.

Bug (2026-08-13, same day): the first cut of 'M.W' resolution used a
calendar-month-local rule (M = the Sunday's calendar month, W = which Sunday
falls in that month's 1st/2nd/3rd/4th 7-day bucket) -- the same approximation
/did's 1n+ Step 1n uses. JM's actual system is a fiscal 4-4-5 week calendar:
week 1 of the year starts the Sunday on/after Jan 1, and every 13-week
quarter splits into fiscal months of 4, 4, then 5 weeks. The two rules agree
most weeks (both give '5.4' -> Sunday 5/24) and silently disagree exactly at
quarter-boundary weeks -- JM caught it live when '6.1' resolved to Wed
6/3-Tue 6/9 (calendar-local) instead of the correct Wed 5/27-Tue 6/2 (fiscal).
These tests pin the corrected fiscal resolution; only 2026 has a confirmed
week-1 anchor (2026-01-04) so other years raise rather than guess.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SKILL = Path(__file__).parent / "SKILL.md"
sys.path.insert(0, str(Path(__file__).parent))
import week_calc  # noqa: E402


def expected_week(today: date) -> tuple[date, date]:
    """Most recent COMPLETE Wed-Tue week as of `today`. week_end <= today."""
    # Tuesday weekday() == 1
    days_back = (today.weekday() - 1) % 7  # 0 if Tue, 1 if Wed, ... 6 if Mon
    week_end = today - timedelta(days=days_back)
    week_start = week_end - timedelta(days=6)
    return week_start, week_end


def test_wednesday_targets_just_completed_week():
    """The bug case: on Wed 2026-04-22 we must target Wed 4/15 - Tue 4/21."""
    today = date(2026, 4, 22)  # Wednesday
    ws, we = expected_week(today)
    assert ws == date(2026, 4, 15), ws
    assert we == date(2026, 4, 21), we
    assert we < today, "week_end must be strictly before today on Wednesday"


def test_tuesday_targets_today():
    today = date(2026, 4, 21)  # Tuesday
    ws, we = expected_week(today)
    assert we == today
    assert ws == date(2026, 4, 15)


def test_other_days_target_most_recent_tuesday():
    for d, expected_end in [
        (date(2026, 4, 23), date(2026, 4, 21)),  # Thu
        (date(2026, 4, 27), date(2026, 4, 21)),  # Mon
        (date(2026, 4, 28), date(2026, 4, 28)),  # Tue (next)
        (date(2026, 4, 29), date(2026, 4, 28)),  # Wed (next)
    ]:
        _, we = expected_week(d)
        assert we == expected_end, f"{d}: got {we}, want {expected_end}"
        assert we <= d


def test_skill_md_has_unambiguous_wednesday_rule():
    """Guard against regressing the SKILL.md prose that confused the model."""
    import re
    text = SKILL.read_text()
    # Must explicitly call out Wednesday and the "do not target today" rule.
    assert re.search(r"Wednesday.*ended.*yesterday", text, re.IGNORECASE | re.DOTALL), \
        "SKILL.md must explicitly say that on Wednesday, the target week ended yesterday."
    assert "Do NOT target the week starting today" in text, \
        "SKILL.md must warn against targeting the week starting today on a Wednesday."
    # Must require a sanity-check before writing.
    assert "Sanity check before writing" in text, \
        "SKILL.md must require a sanity check that week_end <= today."
    assert "week_end > today" in text or "week_end &gt; today" in text, \
        "SKILL.md must call out the week_end>today failure mode."


def test_skill_md_requires_topRow_date_check():
    text = SKILL.read_text()
    assert "B{topRow}" in text and "week_start" in text, \
        "SKILL.md must require verifying B[topRow] == week_start before writing."


# ── New: week_calc.py's arg parsing (M.W label / ISO date / no-arg) ────────

def test_week_calc_no_arg_matches_old_default_logic():
    """week_calc.week_range(None, today) must agree with the pre-existing
    (pinned above) no-arg default for every day-of-week case."""
    cases = [
        date(2026, 4, 22),  # Wed
        date(2026, 4, 21),  # Tue
        date(2026, 4, 23),  # Thu
        date(2026, 4, 27),  # Mon
        date(2026, 4, 28),  # Tue
        date(2026, 4, 29),  # Wed
    ]
    for d in cases:
        assert week_calc.week_range(None, today=d) == expected_week(d), d


def test_week_calc_month_week_label_resolves_to_containing_wed_tue_week():
    # '7.4' -> the Sunday of fiscal week 4 of fiscal month 7, then the
    # Wed-Tue week that Sunday falls inside.
    sunday = week_calc.sunday_for_week_label("7.4")
    ws, we = week_calc.week_range("7.4")
    assert ws.weekday() == 2 and we.weekday() == 1, "range must be Wed..Tue"
    assert ws <= sunday <= we, "the label's Sunday must fall inside the resolved week"
    assert (we - ws).days == 6


def test_week_calc_fiscal_445_quarter_boundary_matches_jm_correction():
    """Regression for the 2026-08-13 bug: '5.4' and '6.1' are CONSECUTIVE
    fiscal weeks straddling a quarter boundary (June is Q2's 3rd/5-week
    month). The old calendar-month-local rule put '6.1' a full week later
    than it should be. Pinned to JM's own live correction during the 1s897
    backfill that surfaced this."""
    assert week_calc.week_range("5.4") == (date(2026, 5, 20), date(2026, 5, 26))
    assert week_calc.week_range("6.1") == (date(2026, 5, 27), date(2026, 6, 2))
    # And the two must be back-to-back with no gap or overlap.
    _, week_5_4_end = week_calc.week_range("5.4")
    week_6_1_start, _ = week_calc.week_range("6.1")
    assert week_6_1_start == week_5_4_end + timedelta(days=1)


def test_week_calc_fiscal_445_full_quarter_sequence_is_consecutive():
    """All 9 weeks JM asked to backfill (5.4, 6.1-6.5, 7.1-7.3) must chain
    into consecutive 7-day blocks with no gaps -- confirms the 4-4-5 split
    (June = fiscal month 6 = Q2's 3rd month = 5 weeks) is wired correctly."""
    labels = ["5.4", "6.1", "6.2", "6.3", "6.4", "6.5", "7.1", "7.2", "7.3"]
    ranges = [week_calc.week_range(l) for l in labels]
    for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:]):
        assert next_start == prev_end + timedelta(days=1)
    assert ranges[0] == (date(2026, 5, 20), date(2026, 5, 26))
    assert ranges[-1] == (date(2026, 7, 15), date(2026, 7, 21))


def test_week_calc_month_3_6_9_12_have_five_weeks():
    """The 3rd month of every quarter (3, 6, 9, 12) absorbs the extra week;
    week 5 must resolve, and week 6 must not."""
    for month in (3, 6, 9, 12):
        week_calc.sunday_for_week_label("%d.5" % month)  # must not raise
        with pytest.raises(ValueError):
            week_calc.sunday_for_week_label("%d.6" % month)


def test_week_calc_unconfirmed_year_raises_rather_than_guesses():
    """Only 2026's fiscal-week-1 anchor is confirmed (Sunday 2026-01-04, per
    JM 2026-08-13). A different year must raise loudly, not silently assume
    the same 'Sunday on/after Jan 1' rule holds -- that rule was given for
    2026 specifically, not confirmed as a general year-over-year pattern."""
    with pytest.raises(ValueError):
        week_calc.week_range("5.4", year=2025)


def test_week_calc_iso_date_resolves_to_containing_wed_tue_week():
    d = date(2026, 8, 13)  # Thursday
    ws, we = week_calc.week_range("2026-08-13")
    assert ws == date(2026, 8, 12) and we == date(2026, 8, 18)
    assert ws <= d <= we


def test_week_calc_iso_date_on_a_wednesday_is_the_week_start_itself():
    ws, we = week_calc.week_range("2026-07-22")  # a Wednesday
    assert ws == date(2026, 7, 22)
    assert we == date(2026, 7, 28)


def test_week_calc_iso_date_on_a_tuesday_is_the_week_end_itself():
    ws, we = week_calc.week_range("2026-07-28")  # a Tuesday
    assert ws == date(2026, 7, 22)
    assert we == date(2026, 7, 28)


def test_week_calc_invalid_week_label_raises():
    with pytest.raises(ValueError):
        week_calc.week_range("2.6")  # fiscal month 2 (not a quarter's 3rd) has only 4 weeks


def test_week_calc_malformed_arg_is_not_treated_as_a_label():
    """A bare non-numeric, non-ISO arg must raise (from date.fromisoformat),
    not be silently swallowed into the no-arg default -- a typo in the week
    arg must be loud, not fall back to 'most recent complete week'."""
    with pytest.raises(ValueError):
        week_calc.week_range("not-a-date")


def test_week_calc_respects_explicit_year_for_week_label():
    """2026 is the only confirmed anchor year; passing it explicitly (vs.
    relying on the today-derived default) must still resolve normally."""
    ws, _ = week_calc.week_range("7.4", year=2026)
    assert ws.year == 2026


def test_skill_md_documents_the_optional_week_arg():
    text = SKILL.read_text()
    assert "week_calc.py" in text, \
        "SKILL.md must call the week_calc.py helper instead of ad-hoc date math"
    assert "M.W" in text, \
        "SKILL.md must document the M.W week-label argument"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
