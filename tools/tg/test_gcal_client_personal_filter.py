"""lx@m5c7.com carries Louisa's personal solo events (call nanny, ...), m5x2
business meetings Jonathan is actually part of (m5x2 Strat), AND meetings
purely between her and someone else on the m5x2 team (HZ/LX 1:1, her 1:1 with
Han Zhao). janus originally showed ALL of it as if it were the user's own
calendar (user report 2026-07-13: "I can't find them on my calendar").

The first fix (2026-07-13) dropped only her SOLO events (no attendees besides
herself). The user then reported (2026-07-15) meetings between her and other
people were still leaking through — _should_hide_lx_event now hides anything
Jonathan isn't actually an attendee of, at fetch time, without hiding the
whole calendar (which would also hide meetings he needs to see)."""
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


def test_solo_event_with_no_attendees_is_hidden():
    mod = _load()
    assert mod._should_hide_lx_event("lx@m5c7.com", "call nanny", None) is True
    assert mod._should_hide_lx_event("lx@m5c7.com", "Call Nanny candidate ", []) is True


def test_solo_event_with_only_self_attendee_is_hidden():
    mod = _load()
    attendees = [{"email": "lx@m5c7.com", "self": True, "organizer": True}]
    assert mod._should_hide_lx_event("lx@m5c7.com", "dentist appointment", attendees) is True


def test_meeting_without_jonathan_is_now_hidden():
    """Regression (2026-07-15): "HZ/LX 1:1" — a real m5x2 meeting, but purely
    between Louisa and Han — must be hidden. The 2026-07-13 fix only checked
    for "any non-self attendee", which let this through; the user reported it
    was still showing."""
    mod = _load()
    attendees = [
        {"email": "hanzhao@m5x2.com", "self": False},
        {"email": "lx@m5c7.com", "self": True, "organizer": True},
    ]
    assert mod._should_hide_lx_event("lx@m5c7.com", "HZ/LX 1:1", attendees) is True


def test_meeting_with_jonathan_as_attendee_survives():
    mod = _load()
    attendees = [
        {"email": "lx@m5c7.com", "self": True},
        {"email": "ian@m5x2.com", "self": False},
        {"email": "mckay@m5c7.com", "self": False, "organizer": True},
    ]
    assert mod._should_hide_lx_event("lx@m5c7.com", "m5x2 Strat (1|1|1)", attendees) is False


def test_jonathan_email_match_is_case_insensitive():
    mod = _load()
    attendees = [{"email": "MCKAY@M5C7.COM", "self": False}]
    assert mod._should_hide_lx_event("lx@m5c7.com", "some meeting", attendees) is False


def test_m5x2_titled_event_survives_even_with_no_attendees():
    """A literal "m5x2" in the title is an unambiguous business signal and
    must win regardless of attendees — a fallback for missing/incomplete
    attendee data."""
    mod = _load()
    assert mod._should_hide_lx_event("lx@m5c7.com", "m5x2 Strat (1|1|1)", []) is False


def test_other_calendars_are_never_filtered():
    """The filter is scoped to lx@m5c7.com only — a solo, no-attendee event on
    any other calendar (the user's own primary, m5x2 Cal, etc.) must survive
    untouched."""
    mod = _load()
    assert mod._should_hide_lx_event("primary", "call nanny", []) is False
    assert mod._should_hide_lx_event("m5x2 Cal", "call nanny", []) is False


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
