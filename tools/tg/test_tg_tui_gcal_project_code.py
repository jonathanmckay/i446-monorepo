"""gcal_project_code: a literal "m5x2" in an event title must win over the
calendar-level CALENDAR_PROJECT_MAP default.

Bug (2026-07-12): "m5x2 Strat (1|1|1)" rendered xk88-orange instead of
m5x2-crimson. Root cause: the event lives on the "lx@m5c7.com" calendar
(Louisa is on the m5x2 team, so m5x2 business invites show there too), and
CALENDAR_PROJECT_MAP hardcodes that calendar to xk88 (Louisa's PERSONAL
1:1/social entries). The calendar map was checked before title keywords, so
the calendar-level default silently overrode an event whose title says
"m5x2" outright."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_gcalcode", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_gcalcode"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_m5x2_title_on_lx_calendar_resolves_to_m5x2():
    mod = _load_tui()
    ev = {"calendar": "lx@m5c7.com", "title": "m5x2 Strat (1|1|1)"}
    assert mod.gcal_project_code(ev) == "m5x2"


def test_m5x2_title_beats_generic_11_keyword():
    """The title also contains "1|1", which is an i9 keyword — "m5x2" must
    still win, not just beat the calendar map."""
    mod = _load_tui()
    ev = {"calendar": "", "title": "m5x2 Analytics and Accounting"}
    assert mod.gcal_project_code(ev) == "m5x2"


def test_lx_personal_event_without_m5x2_in_title_still_xk88():
    """Non-m5x2 events on lx@m5c7.com (Louisa's genuinely personal invites)
    must keep resolving via the calendar map — this fix must not blanket the
    whole calendar as m5x2."""
    mod = _load_tui()
    ev = {"calendar": "lx@m5c7.com", "title": "lx dentist appointment"}
    assert mod.gcal_project_code(ev) == "xk88"


def test_m5x2_cal_1on1_without_m5x2_word_still_resolves_via_calendar_map():
    """"IM|JM 1|1" has no "m5x2" in its title but lives on "m5x2 Cal" — must
    still resolve to m5x2 via the calendar map (title-keyword check is a new
    ADDITIONAL priority, not a replacement for the calendar map)."""
    mod = _load_tui()
    ev = {"calendar": "m5x2 Cal", "title": "IM|JM 1|1"}
    assert mod.gcal_project_code(ev) == "m5x2"


def test_generic_11_keyword_still_resolves_to_i9_absent_m5x2_and_calendar():
    mod = _load_tui()
    ev = {"calendar": "", "title": "weekly 1:1"}
    assert mod.gcal_project_code(ev) == "i9"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
