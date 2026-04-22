"""
Regression test for /1s897 week selection.

Bug (2026-04-22): When run on a Wednesday, the skill wrote the social review
to next week's row (Wed 4/22 - Tue 4/28) instead of the just-completed week
(Wed 4/15 - Tue 4/21). Root cause was ambiguous wording in Step 1 of SKILL.md
that let the model treat "today" as the start of the target week on Wednesdays.

This test pins the day-of-week → (week_start, week_end) mapping the skill is
required to follow, and asserts the SKILL.md text contains the corrected,
unambiguous rules.
"""

from datetime import date, timedelta
from pathlib import Path
import re

SKILL = Path(__file__).parent / "SKILL.md"


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
