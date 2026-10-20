"""User request 2026-07-30: '^X deletes an item from Janus — and it won't
affect the calendar for outlook, but will for m5x2.' Google-hosted events
are API-deleted (gcal_client.delete_event); Outlook rows (Agency fetch or
the read-only MSFT Slow Sync ICS import) are hidden locally only. The hide
key matches fetch_gcal's cross-calendar dedupe key so a mirrored copy from
the other source can't resurface a deleted meeting."""
import datetime as dtm
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_del", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_del"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ev(title="Standup", h=9, **extra):
    start = dtm.datetime.now(TZ).replace(hour=h, minute=0, second=0, microsecond=0)
    return {"title": title, "start_dt": start,
            "end_dt": start + dtm.timedelta(minutes=30), **extra}


# ─── deletability classification ────────────────────────────────────────────

def test_m5x2_google_event_is_deletable():
    mod = _load_tui()
    ev = _ev(id="abc123", calendar_id="mckay@m5c7.com")
    assert mod._event_gcal_deletable(ev)


def test_outlook_agency_event_not_deletable():
    """Agency-fetched Outlook events carry no Google id at all."""
    mod = _load_tui()
    ev = _ev(calendar="Outlook")
    assert not mod._event_gcal_deletable(ev)


def test_msft_slow_sync_import_not_deletable():
    """The ICS import mirror is read-only — deleting there is impossible and
    wouldn't touch the real Outlook event anyway."""
    mod = _load_tui()
    ev = _ev(id="abc123",
             calendar_id="l20n3a79v2lq68fod4de3lvp1ba2iqft@import.calendar.google.com")
    assert not mod._event_gcal_deletable(ev)


def test_stale_cache_event_without_id_not_deletable():
    """Pre-2026-07-30 gcal caches carry no id — must fall back to hide."""
    mod = _load_tui()
    ev = _ev(calendar_id="mckay@m5c7.com")
    assert not mod._event_gcal_deletable(ev)


# ─── hide persistence ───────────────────────────────────────────────────────

def test_hide_round_trips_and_filters(tmp_path):
    mod = _load_tui()
    mod.HIDDEN_EVENTS = tmp_path / "hidden.json"
    ev = _ev("Huddle: XBOX Developer")
    mod._hide_event(ev)
    hidden = mod._load_hidden_events()
    assert mod._hidden_event_key(ev) in hidden
    # a same-meeting copy from the other calendar (case/whitespace of the
    # dedupe key) is filtered by the same key
    copy = {**ev, "title": "huddle: xbox developer".upper().lower()}
    assert mod._hidden_event_key(copy) in hidden
    other = _ev("Lunch hold", h=12)
    assert mod._hidden_event_key(other) not in hidden


def test_hide_prunes_old_entries(tmp_path):
    mod = _load_tui()
    mod.HIDDEN_EVENTS = tmp_path / "hidden.json"
    old = _ev("Ancient standup")
    old_start = old["start_dt"] - dtm.timedelta(days=mod.HIDDEN_EVENTS_KEEP_DAYS + 5)
    old["start_dt"], old["end_dt"] = old_start, old_start + dtm.timedelta(minutes=30)
    mod._hide_event(old)
    mod._hide_event(_ev("Fresh standup"))
    data = json.loads(mod.HIDDEN_EVENTS.read_text())
    titles = [k[0] for k in data["hidden"]]
    assert "fresh standup" in titles
    assert "ancient standup" not in titles


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
