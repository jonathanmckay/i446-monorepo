"""lx@m5c7.com is Louisa's calendar, visible on the m5c7 account. History:
2026-07-13 hid her solo events; 2026-07-15 hid meetings Jonathan isn't an
attendee of; 2026-07-30 the user asked for the whole calendar gone ("make it
so that Janus does not show Louisa's calendar"). The blanket skip is safe
because meetings Jonathan is part of also live on his own calendar (he's an
attendee), so nothing he needs disappears with hers."""
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


def test_lx_calendar_is_filtered_entirely():
    mod = _load()
    assert mod._calendar_filtered("lx@m5c7.com", "lx@m5c7.com") is True


def test_lx_calendar_matches_on_id_when_summary_is_renamed():
    """A display-name change on the calendar must not resurface it."""
    mod = _load()
    assert mod._calendar_filtered("Louisa", "lx@m5c7.com") is True


def test_lx_personal_gmail_calendar_is_filtered():
    """Louisa has TWO calendars on this account — the 2026-07-30 leak
    ("it's still showing...") was her personal gmail one."""
    mod = _load()
    assert mod._calendar_filtered("lxu888", "lxu888@gmail.com") is True


def test_other_calendars_are_never_filtered():
    mod = _load()
    assert mod._calendar_filtered("primary", "mckay@m5c7.com") is False
    assert mod._calendar_filtered("m5x2 Cal", "m5x2cal@group.calendar.google.com") is False
    assert mod._calendar_filtered(
        "MSFT (Slow Sync)",
        "l20n3a79v2lq68fod4de3lvp1ba2iqft@import.calendar.google.com") is False


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
