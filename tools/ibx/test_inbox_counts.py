"""inbox_counts: per-habit pending counts + success flags (2026-11-12).

JM: show `ibx i9 (4) [20] ✉3` in dtd and close the card only when every
source behind it was pinged successfully and came back empty. The old
first-inbox-zero-of-the-day path closed all six habits even when a source
had failed or was unreachable on the host.
"""
import json
import time
from pathlib import Path

import inbox_counts as ic


def _email(acct):
    return {"type": "email", "_data": {"email": {"_account": acct, "id": "x"}}}


def _slack(ws):
    return {"type": "slack", "_data": {"workspace": ws, "thread": {}}}


def test_channel_of_maps_every_queue_type():
    assert ic.channel_of(_email("m5c7")) == "gmail:m5c7"
    assert ic.channel_of(_email("gmail")) == "gmail:gmail"
    assert ic.channel_of({"type": "imsg", "_data": {}}) == "imsg"
    assert ic.channel_of(_slack("github")) == "slack:github"
    assert ic.channel_of({"type": "outlook", "_data": {}}) == "outlook"
    assert ic.channel_of({"type": "teams", "_data": {}}) == "teams"
    assert ic.channel_of({"type": "weird"}) is None


def test_every_channel_in_the_map_is_reachable_from_channel_of():
    # a typo in HABIT_CHANNELS would silently make a habit uncountable
    seen = {ic.channel_of(_email("m5c7")), ic.channel_of(_email("gmail")),
            "imsg", ic.channel_of(_slack("github")), ic.channel_of(_slack("m5x2")),
            "outlook", "teams"}
    assert ic.ALL_CHANNELS <= seen


def test_snapshot_counts_per_habit_and_requires_every_channel_ok():
    items = [_email("m5c7"), _email("m5c7"), _email("gmail"), _slack("m5x2")]
    ok = {"gmail:m5c7": True, "gmail:gmail": True, "imsg": False,
          "slack:m5x2": True}
    snap = ic.build_snapshot(items, ok, host="ix", now=1000.0)
    h = snap["habits"]
    assert h["ibx m5x2"] == {"count": 2, "ok": True}
    assert h["slack m5x2"] == {"count": 1, "ok": True}
    # ibx s897 spans gmail:gmail + imsg; imsg failed -> count kept, ok False
    assert h["ibx s897"] == {"count": 1, "ok": False}
    # channels this host never fetched are absent, not zero
    for absent in ("ibx i9", "teams", "slack i9"):
        assert absent not in h
    assert snap["ts"] == 1000.0 and snap["host"] == "ix"


def test_channel_started_but_never_finished_is_not_ok():
    snap = ic.build_snapshot([], {"gmail:m5c7": None}, host="h", now=1.0)
    assert snap["habits"]["ibx m5x2"] == {"count": 0, "ok": False}


def test_zero_habits_only_lists_ok_and_empty():
    snap = {"habits": {"ibx m5x2": {"count": 0, "ok": True},
                       "ibx i9": {"count": 0, "ok": False},
                       "slack m5x2": {"count": 2, "ok": True}}}
    assert ic.zero_habits(snap) == ["ibx m5x2"]


def test_write_is_atomic_and_read_merges_hosts_newest_wins(tmp_path):
    now = 10_000.0
    a = ic.build_snapshot([_email("m5c7")], {"gmail:m5c7": True}, host="ix", now=now - 60)
    b = ic.build_snapshot([], {"gmail:m5c7": True, "outlook": True}, host="straylight", now=now - 30)
    ic.write_snapshot(a, tmp_path)
    ic.write_snapshot(b, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "inbox-counts-ix.json", "inbox-counts-straylight.json"]
    badges = ic.read_badges(tmp_path, now=now)
    # straylight is newer for ibx m5x2, and the only source for ibx i9
    assert badges["ibx m5x2"]["count"] == 0 and badges["ibx m5x2"]["host"] == "straylight"
    assert badges["ibx i9"] == {"count": 0, "ok": True, "ts": now - 30, "host": "straylight"}


def test_stale_snapshot_is_ignored(tmp_path):
    now = 50_000.0
    old = ic.build_snapshot([], {"gmail:m5c7": True}, host="ix", now=now - ic.MAX_AGE_S - 1)
    ic.write_snapshot(old, tmp_path)
    assert ic.read_badges(tmp_path, now=now) == {}


def test_corrupt_file_does_not_break_the_reader(tmp_path):
    (tmp_path / "inbox-counts-bad.json").write_text("{not json")
    (tmp_path / "inbox-counts-list.json").write_text("[1,2]")
    good = ic.build_snapshot([], {"imsg": True, "gmail:gmail": True}, host="ok", now=99.0)
    ic.write_snapshot(good, tmp_path)
    assert ic.read_badges(tmp_path, now=100.0)["ibx s897"]["count"] == 0


def test_badge_text_and_norm():
    assert ic.badge_text({"count": 3, "ok": True}) == "✉3"
    assert ic.badge_text({"count": 0, "ok": True}) == "✉0"
    assert ic.badge_text({"count": 3, "ok": False}) == ""
    assert ic.badge_text(None) == ""
    assert ic.norm("ibx - s897") == "ibx s897"
    assert ic.norm("Slack  M5x2") == "slack m5x2"


def test_host_slug_matches_dtd_rule():
    assert ic.host_slug("ix.local") == "ix-local"
    assert ic.host_slug("Straylight-Refit.local") == "straylight-refit-local"
    assert len(ic.host_slug("a" * 40)) == 24


def test_prefer_host_beats_a_newer_foreign_snapshot(tmp_path):
    now = 20_000.0
    mine = ic.build_snapshot([_email("m5c7")], {"gmail:m5c7": True}, host="ix-local", now=now - 120)
    theirs = ic.build_snapshot([], {"gmail:m5c7": True, "outlook": True}, host="straylight", now=now - 10)
    ic.write_snapshot(mine, tmp_path); ic.write_snapshot(theirs, tmp_path)
    b = ic.read_badges(tmp_path, now=now, prefer_host="ix-local")
    assert b["ibx m5x2"]["host"] == "ix-local" and b["ibx m5x2"]["count"] == 1
    # a habit my host never reports still comes from the other host
    assert b["ibx i9"]["host"] == "straylight"
    # a STALE own snapshot does not win
    stale = ic.build_snapshot([], {"gmail:m5c7": True}, host="ix-local", now=now - ic.MAX_AGE_S - 5)
    ic.write_snapshot(stale, tmp_path)
    assert ic.read_badges(tmp_path, now=now, prefer_host="ix-local")["ibx m5x2"]["host"] == "straylight"
