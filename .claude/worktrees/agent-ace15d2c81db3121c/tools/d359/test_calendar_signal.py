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
