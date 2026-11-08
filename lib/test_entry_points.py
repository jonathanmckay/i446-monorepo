"""Shared per-entry 分 (lib/entry_points.py), JM 2026-11-05."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_points as ep  # noqa: E402

DAY = "2026-11-05"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTRY_POINTS_DIR", str(tmp_path))
    return tmp_path


def _line(path, ts, id_, pts, day=DAY):
    with open(path, "a") as f:
        f.write(json.dumps({"ts": ts, "day": day, "id": id_, "pts": pts}) + "\n")


def test_a_point_recorded_on_one_host_is_read_on_every_host(store):
    _line(store / "entry-points.ix.jsonl", "2026-11-05T08:00:00", "7", 19)
    assert ep.for_day(DAY) == {"7": 19}


def test_the_newest_line_wins_across_hosts(store):
    _line(store / "entry-points.ix.jsonl", "2026-11-05T09:00:00", "7", 25)
    _line(store / "entry-points.straylight.jsonl", "2026-11-05T08:00:00", "7", 19)
    assert ep.for_day(DAY) == {"7": 25}


def test_a_tombstone_removes_the_local_sidecar_value_too(store):
    ep.record(DAY, {"7": 19})
    ep.record(DAY, {"7": None})
    assert ep.overlay({"7": 19, "8": 5}, DAY) == {"8": 5}


def test_overlay_beats_a_stale_local_value(store):
    ep.record(DAY, {"7": 25})
    assert ep.overlay({"7": 19}, DAY) == {"7": 25}


def test_other_days_stay_out(store):
    ep.record("2026-11-04", {"7": 19})
    assert ep.for_day(DAY) == {}


def test_a_half_synced_line_is_skipped(store):
    _line(store / "entry-points.ix.jsonl", "2026-11-05T08:00:00", "7", 19)
    with open(store / "entry-points.ix.jsonl", "a") as f:
        f.write('{"ts": "2026')
    assert ep.for_day(DAY) == {"7": 19}


def test_a_new_line_is_seen_through_the_cache(store):
    ep.record(DAY, {"7": 19})
    assert ep.for_day(DAY) == {"7": 19}
    ep.record(DAY, {"8": 5})
    assert ep.for_day(DAY) == {"7": 19, "8": 5}


def test_tests_never_touch_the_real_store(monkeypatch):
    monkeypatch.delenv("ENTRY_POINTS_DIR", raising=False)
    assert ep._dir() is None
    assert ep.for_day(DAY) == {}
    ep.record(DAY, {"7": 19})        # no-op, no write


def test_record_never_raises_on_an_unwritable_dir(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("ENTRY_POINTS_DIR", str(blocker / "sub"))
    ep.record(DAY, {"7": 19})


# ── open credits: 1 i447 closed in dtd, its time entered afterwards ──────────
import datetime as dt  # noqa: E402

TODAY = dt.date(2026, 11, 5)
SAT = dt.date(2026, 11, 7)


def _entry(id_="9", desc="1 i447", when=(2026, 11, 5, 18, 0), at="2099-01-01T00:00:00+00:00", **kw):
    return {"id": id_, "desc": desc, "start_dt": dt.datetime(*when), "at": at, "running": False, **kw}


def _credit(cid="t@2026-11-05"):
    ep.record_credit(cid, ["1 i447 10.31 (20) [25]"], 25, TODAY, SAT)


@pytest.mark.parametrize("raw", ["1 i447 10.31 (20) [25]", "1 i447", "1 i447 @i447", "1 I447 10/31"])
def test_normalize_drops_annotations_and_the_date_suffix(raw):
    assert ep.normalize(raw) == "1 i447"


def test_a_post_hoc_entry_takes_the_points(store):
    _credit()
    assert ep.link([_entry()], TODAY) == {"9": 25}
    assert ep.for_day(DAY) == {"9": 25}


def test_a_credit_is_taken_once_by_the_earliest_entry(store):
    _credit()
    late, early = _entry("8", when=(2026, 11, 5, 20, 0)), _entry("9", when=(2026, 11, 5, 18, 0))
    assert ep.link([late, early], TODAY) == {"9": 25}
    assert ep.link([late, early], TODAY) == {}


def test_entries_made_before_the_close_are_left_alone(store):
    _credit()
    assert ep.link([_entry(at="2026-01-01T00:00:00+00:00")], TODAY) == {}


def test_next_cycle_is_a_new_session(store):
    _credit()
    assert ep.link([_entry(when=(2026, 11, 8, 9, 0))], dt.date(2026, 11, 8)) == {}


def test_running_timers_and_other_names_wait(store):
    _credit()
    assert ep.link([_entry(running=True), _entry("7", desc="1 hcb")], TODAY) == {}


def test_a_recorded_entry_is_not_overwritten(store):
    _credit()
    ep.record(DAY, {"9": 40})
    assert ep.link([_entry()], TODAY) == {} and ep.for_day(DAY) == {"9": 40}


def test_claim_takes_the_credit_at_done_without_at(store):
    _credit()
    assert ep.claim("9", "1 i447", TODAY) == 25
    assert ep.claim("8", "1 i447", TODAY) is None


def test_undo_voids_the_credit_and_clears_its_entry(store):
    _credit()
    ep.link([_entry()], TODAY)
    ep.void_credit("t@2026-11-05")
    assert ep.for_day(DAY) == {"9": None}
    ep.void_credit("gone@x")     # unknown id: no-op
    assert ep.link([_entry("8")], TODAY) == {}


def test_a_voided_credit_links_nothing(store):
    _credit()
    ep.void_credit("t@2026-11-05")
    assert ep.link([_entry()], TODAY) == {} and ep.claim("9", "1 i447", TODAY) is None


def test_credit_lines_stay_out_of_entry_records(store):
    _credit()
    assert ep.for_day(DAY) == {}


def test_no_credits_is_a_no_op_and_tests_stay_out(monkeypatch):
    monkeypatch.delenv("ENTRY_POINTS_DIR", raising=False)
    assert ep.link([_entry()], TODAY) == {} and ep.claim("9", "1 i447", TODAY) is None


# ── rule (a), Dream v180: a credit goes only to an entry started on or before
# the close day, in link() and claim() alike ─────────────────────────────────
MON, WED = dt.date(2026, 11, 2), dt.date(2026, 11, 4)


def _weekly(day=MON, weekly=True, cid="1hcb@2026-11-02"):
    ep.record_credit(cid, ["1 hcb (60) [60]"], 60, day, SAT, weekly=weekly)


def test_a_mid_week_catch_up_does_not_take_mondays_weekly_credit(store):
    """Monday's 1 hcb closed with no entry; Wednesday's session is week_fill's
    catch-up of last week, so it must log on its own."""
    _weekly()
    wed = _entry("9", desc="1 hcb", when=(2026, 11, 4, 7, 0))
    assert ep.link([wed], WED) == {}
    assert ep.claim("9", "1 hcb", WED) is None
    assert ep.for_day("2026-11-04") == {}


def test_an_entry_on_the_close_day_still_takes_the_credit(store):
    _weekly()
    assert ep.link([_entry("9", desc="1 hcb", when=(2026, 11, 2, 7, 0))], WED) == {"9": 60}


def test_a_weekly_credit_takes_a_backfill_from_earlier_in_its_week(store):
    _weekly()
    sun = _entry("9", desc="1 hcb", when=(2026, 11, 1, 7, 0))
    last_week = _entry("8", desc="1 hcb", when=(2026, 10, 31, 7, 0))
    assert ep.link([last_week, sun], WED) == {"9": 60}


def test_a_saturday_close_is_still_weekly(store):
    """_credit_until gives until == day on a Saturday: the flag, not the
    dates, says the card is weekly."""
    _weekly(day=SAT, cid="1hcb@2026-11-07")
    assert ep.claim("9", "1 hcb", WED) == 60


def test_a_daily_credit_takes_only_its_own_day(store):
    ep.record_credit("d@2026-11-05", ["1 i447"], 25, TODAY, TODAY, weekly=False)
    assert ep.link([_entry("8", when=(2026, 11, 4, 9, 0))], TODAY) == {}
    assert ep.claim("8", "1 i447", dt.date(2026, 11, 4)) is None
    assert ep.claim("9", "1 i447", TODAY) == 25


def test_old_lines_without_the_flag_infer_weekly_from_until(store):
    _credit()                    # TODAY..SAT, no flag: weekly
    assert ep.claim("9", "1 i447", dt.date(2026, 11, 6)) is None
    assert ep.claim("9", "1 i447", dt.date(2026, 11, 3)) == 25


def test_claim_checks_the_entry_was_made_after_the_close(store):
    _credit()
    assert ep.claim("9", "1 i447", TODAY, at="2026-01-01T00:00:00+00:00") is None
    assert ep.claim("9", "1 i447", TODAY, at="2099-01-01T00:00:00+00:00") == 25
