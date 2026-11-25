"""Duplicate finding and --apply parsing for /1cal. No network."""
import importlib.util
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "onecal", os.path.join(os.path.dirname(__file__), "1cal.py"))
onecal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(onecal)

JBM, M5 = onecal.JBM, onecal.M5X2


def ev(id, uid, start="2026-12-08T19:00:00-08:00", organizer=JBM, attendees=(), **kw):
    e = {"id": id, "iCalUID": uid, "summary": kw.pop("summary", "dinner"),
         "start": {"dateTime": start}, "organizer": {"email": organizer},
         "attendees": [{"email": a} for a in attendees]}
    e.update(kw)
    return e


def rec(id, toggl_id, src, created):
    return {"id": id, "iCalUID": id + "@google.com", "summary": "o314 @hcm", "created": created,
            "start": {"dateTime": "2026-10-17T06:00:00-07:00"},
            "extendedProperties": {"private": {"toggl_id": toggl_id, "src": src}}}


def test_record_dupes_keep_the_nightly_copy():
    items = onecal.find_record_dupes([
        rec("a", "1", "0r-backfill", "2026-10-18T01:00:00Z"),
        rec("b", "1", "0r", "2026-10-18T09:00:00Z"),
        rec("c", "2", "0r", "2026-10-18T09:00:00Z"),
    ])
    assert len(items) == 1
    assert items[0]["keep_id"] == "b"
    assert [r["id"] for r in items[0]["remove"]] == ["a"]


def test_same_title_different_toggl_id_is_not_a_duplicate():
    assert onecal.find_record_dupes([rec("a", "1", "0r", "x"), rec("b", "2", "0r", "x")]) == []


def test_jm_organizer_keeps_organizer_copy_and_drops_alias_guest():
    # The gmail-organized copy lists the m5x2.com alias, not the m5c7 address.
    a = ev("x", "u1", organizer=JBM, attendees=[JBM, "mckay@m5x2.com", "lxu888@gmail.com"])
    b = ev("x", "u1", organizer=JBM, attendees=[JBM, "mckay@m5x2.com", "lxu888@gmail.com"])
    (item,) = onecal.find_invite_dupes([a], [b])
    assert (item["keep"], item["action"], item["auto"]) == ("jbm", "drop_attendee", True)


def test_keeping_the_non_organizer_side_is_manual():
    a = ev("x", "u1", organizer=M5, attendees=[M5, JBM])
    (item,) = onecal.find_invite_dupes([a], [dict(a)])
    assert item["keep"] == "m5x2"
    onecal.plan_invite(item, "jbm")
    assert (item["action"], item["auto"]) == ("manual", False)


def test_third_party_organizer_goes_personal_unless_business_domain():
    a = ev("x", "u1", organizer="friend@gmail.com", attendees=[JBM, M5])
    (item,) = onecal.find_invite_dupes([a], [dict(a)])
    assert (item["keep"], item["lose"], item["action"]) == ("jbm", "m5x2", "delete_copy")
    b = ev("y", "u2", organizer="lx@m5c7.com", attendees=[JBM, M5])
    (item,) = onecal.find_invite_dupes([b], [dict(b)])
    assert item["keep"] == "m5x2"


def test_partly_shared_recurring_series_is_manual():
    a1 = ev("m_1", "u1", start="2026-12-01T09:00:00-08:00", organizer="f@x.com", recurringEventId="m")
    a2 = ev("m_2", "u1", start="2026-12-08T09:00:00-08:00", organizer="f@x.com", recurringEventId="m")
    (item,) = onecal.find_invite_dupes([a1, a2], [dict(a1)])
    assert (item["action"], item["auto"], item["ids"]["jbm"]) == ("manual", False, "m")


def test_archive_events_are_never_invites():
    r = rec("a", "1", "0r", "x")
    assert onecal.find_invite_dupes([r], [dict(r)]) == []


def test_parse_apply():
    items = [{"kind": "invite", "auto": True}, {"kind": "invite", "auto": False},
             {"kind": "record", "auto": True}]
    assert onecal.parse_apply("1, 2=jbm", items) == [(1, None), (2, "jbm")]
    assert onecal.parse_apply("records", items) == [(3, None)]
    assert onecal.parse_apply("all,1", items) == [(1, None), (3, None)]
    for bad in ("4", "x", "1=work", "3=jbm"):
        with pytest.raises(ValueError):
            onecal.parse_apply(bad, items)
