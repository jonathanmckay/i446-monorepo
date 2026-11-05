#!/usr/bin/env python3
"""Regression: the Google Calendar signal in refresh_last_contact must match
work 1:1s by the '<INITIALS>:JM' title convention and by work-email attendance,
while staying fail-safe against initials collisions and personal-event noise.

Context (2026-06-24): the MSFT (Slow Sync) ICS import strips attendees, so work
1:1s like 'JA:JM 1:1' are matchable only by their initials title. The primary
calendar keeps attendees, but a spouse's personal `email:` appears on family
events ('Imperial treasure', kid camps) that are not outreach — so attendee
matching is restricted to work_email/teams_upn.
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "rlc", HERE / "refresh_last_contact.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


# ── initials map ─────────────────────────────────────────────────────────────

def test_unambiguous_initials_resolve():
    imap = M._build_initials_map({"jessica-allen", "colin-white", "bei-lu"})
    assert imap["ja"] == "jessica-allen"
    assert imap["cw"] == "colin-white"
    assert imap["bl"] == "bei-lu"


def test_ambiguous_initials_dropped():
    # Two distinct 'ja' contacts → matches neither (fail-safe).
    imap = M._build_initials_map({"jessica-allen", "james-adams"})
    assert "ja" not in imap


def test_alias_overrides_mechanical_initials_collision():
    # Both slugs mechanically yield 'lr'; the user's alias claims it for Leeroy.
    imap = M._build_initials_map({"lee-redden", "leeroy-phillips"})
    assert imap["lr"] == "leeroy-phillips"


def test_jm_self_token_never_a_contact():
    imap = M._build_initials_map({"john-meyer"})  # initials 'jm'
    assert "jm" not in imap


# ── event matching ───────────────────────────────────────────────────────────

IMAP = {"ja": "jessica-allen", "lr": "leeroy-phillips", "im": "ian-mckibben"}
EMAP = {"lawrence@m5c7.com": "lawrence-uttke"}


def test_initials_before_jm():
    assert M._match_event("JA:JM 1:1", [], IMAP, EMAP) == {"jessica-allen"}


def test_initials_after_jm_and_pipe_separator():
    assert M._match_event("JM:LR 1:1 ", [], IMAP, EMAP) == {"leeroy-phillips"}
    assert M._match_event("IM|JM 1|1", [], IMAP, EMAP) == {"ian-mckibben"}


def test_spaces_around_separator():
    assert M._match_event("JA : JM 1:1", [], IMAP, EMAP) == {"jessica-allen"}


def test_self_meeting_matches_nobody():
    assert M._match_event("JM : JM 1:1", [], IMAP, EMAP) == set()


def test_noise_title_matches_nobody():
    # Org-wide OOF/standup noise must not match despite containing names.
    assert M._match_event("COLIN OOF (KAUAI)", [], IMAP, EMAP) == set()
    assert M._match_event("XBOX XTEAM Stand Up", [], IMAP, EMAP) == set()


def test_work_email_attendee_matches():
    assert M._match_event("m5x2 Analytics and Accounting",
                          ["lawrence@m5c7.com"], IMAP, EMAP) == {"lawrence-uttke"}


# ── email map only keeps work addresses ──────────────────────────────────────

def test_email_map_excludes_personal_email(tmp_path):
    spouse = tmp_path / "louisa-xu-d359.md"
    spouse.write_text(
        "---\ntitle: \"Louisa Xu\"\nchannels:\n  email: lxu888@gmail.com\n"
        "cadence: weekly\n---\nbody\n")
    staff = tmp_path / "lawrence-uttke-d359.md"
    staff.write_text(
        "---\ntitle: \"Lawrence\"\nchannels:\n  work_email: lawrence@m5c7.com\n"
        "---\nbody\n")
    emap = M._build_email_map([spouse, staff])
    assert "lxu888@gmail.com" not in emap          # personal email ignored
    assert emap.get("lawrence@m5c7.com") == "lawrence-uttke"


def test_teams_upn_is_matched(tmp_path):
    f = tmp_path / "jessica-allen-d359.md"
    f.write_text(
        "---\ntitle: \"Jessica Allen\"\nchannels:\n"
        "  teams_upn: jessicaallen@microsoft.com\n---\nbody\n")
    emap = M._build_email_map([f])
    assert emap.get("jessicaallen@microsoft.com") == "jessica-allen"


# ── only meetings that have already ended count (2026-11-02) ────────────────

from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz

_PST = _tz(_td(hours=-8))
_NOW = _dt(2026, 11, 2, 2, 36, tzinfo=_PST)


def _ev(start, end):
    return {"start": {"dateTime": start}, "end": {"dateTime": end}}


def test_meeting_later_today_not_counted():
    ev = _ev("2026-11-02T13:30:00-08:00", "2026-11-02T14:00:00-08:00")
    assert M._event_contact_date(ev, _NOW) is None


def test_meeting_in_progress_not_counted():
    now = _dt(2026, 11, 2, 13, 45, tzinfo=_PST)
    ev = _ev("2026-11-02T13:30:00-08:00", "2026-11-02T14:00:00-08:00")
    assert M._event_contact_date(ev, now) is None


def test_meeting_ended_today_counted():
    now = _dt(2026, 11, 2, 14, 1, tzinfo=_PST)
    ev = _ev("2026-11-02T13:30:00-08:00", "2026-11-02T14:00:00-08:00")
    assert M._event_contact_date(ev, now) == _date(2026, 11, 2)


def test_yesterday_meeting_counted_utc_input():
    ev = _ev("2026-11-01T21:00:00Z", "2026-11-01T21:30:00Z")
    assert M._event_contact_date(ev, _NOW) == _date(2026, 11, 1)


def test_all_day_today_not_counted_until_tomorrow():
    ev = {"start": {"date": "2026-11-02"}, "end": {"date": "2026-11-03"}}
    assert M._event_contact_date(ev, _NOW) is None
    ev = {"start": {"date": "2026-11-01"}, "end": {"date": "2026-11-02"}}
    assert M._event_contact_date(ev, _NOW) == _date(2026, 11, 1)


# ── v177 regressions: EL alias + no future stamps ───────────────────────────

def test_el_is_eldon_es_is_elliot():
    # 'EL:JM' = Eldon Lei, 'ES:JM' = Elliot Silvers (v177 mis-mapped EL).
    imap = M._build_initials_map({"eldon-lei", "elliot-silvers"})
    assert imap["el"] == "eldon-lei"
    assert imap["es"] == "elliot-silvers"


def test_el_es_survive_a_colliding_contact():
    # A second 'el'/'es' contact must not make the 1:1 shorthands ambiguous.
    imap = M._build_initials_map({"eldon-lei", "elliot-silvers",
                                  "emma-lin", "eric-smith"})
    assert imap["el"] == "eldon-lei"
    assert imap["es"] == "elliot-silvers"


def test_later_today_meeting_not_stamped(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    past = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = [{"summary": "JA:JM 1:1", "start": {"dateTime": past}},
             {"summary": "BL:JM 1:1", "start": {"dateTime": future}}]
    seen = {}

    class _Req:
        def __init__(self, kw): seen.update(kw)
        def execute(self): return {"items": items}

    class _Svc:
        def events(self): return self
        def list(self, **kw): return _Req(kw)

    monkeypatch.setattr(M, "_gcal_service", lambda: _Svc())
    monkeypatch.setattr(M, "GCAL_CAL_IDS", ["x"])
    out = M._fetch_calendar_signal(7, {"jessica-allen", "bei-lu"}, [])
    assert "jessica-allen" in out
    assert "bei-lu" not in out
    assert seen["timeMax"] <= future
