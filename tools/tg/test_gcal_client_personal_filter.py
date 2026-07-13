"""lx@m5c7.com carries both Louisa's personal solo events (call nanny, ...)
and real m5x2 business meetings (she's on the m5x2 team). tg-tui showed her
"call nanny" / "Call Nanny candidate" entries — private reminders with no
other attendees — as if they were on the user's own calendar (user report
2026-07-13: "I can't find them on my calendar" — because they aren't; they're
Louisa's solo events, surfaced only because tg-tui reads her calendar too).

_is_personal_solo_event filters those out at fetch time without hiding the
whole calendar, which would also hide legitimate m5x2 meetings living there."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("gcal_client_filtertest", HERE / "gcal_client.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gcal_client_filtertest"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_solo_event_with_no_attendees_is_filtered():
    mod = _load()
    assert mod._is_personal_solo_event("lx@m5c7.com", "call nanny", None) is True
    assert mod._is_personal_solo_event("lx@m5c7.com", "Call Nanny candidate ", []) is True


def test_solo_event_with_only_self_attendee_is_filtered():
    mod = _load()
    attendees = [{"email": "lx@m5c7.com", "self": True, "organizer": True}]
    assert mod._is_personal_solo_event("lx@m5c7.com", "dentist appointment", attendees) is True


def test_meeting_with_other_attendee_survives_even_without_mckay():
    """"HZ/LX 1:1" — a real m5x2 meeting between two OTHER people — must not
    be filtered just because Jonathan isn't invited."""
    mod = _load()
    attendees = [
        {"email": "hanzhao@m5x2.com", "self": False},
        {"email": "lx@m5c7.com", "self": True, "organizer": True},
    ]
    assert mod._is_personal_solo_event("lx@m5c7.com", "HZ/LX 1:1", attendees) is False


def test_m5x2_titled_event_survives_even_with_no_attendees():
    """A literal "m5x2" in the title is an unambiguous business signal and
    must win regardless of attendee count."""
    mod = _load()
    assert mod._is_personal_solo_event("lx@m5c7.com", "m5x2 Strat (1|1|1)", []) is False


def test_other_calendars_are_never_filtered():
    """The filter is scoped to lx@m5c7.com only — a solo, no-attendee event on
    any other calendar (the user's own primary, m5x2 Cal, etc.) must survive
    untouched."""
    mod = _load()
    assert mod._is_personal_solo_event("primary", "call nanny", []) is False
    assert mod._is_personal_solo_event("m5x2 Cal", "call nanny", []) is False


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
