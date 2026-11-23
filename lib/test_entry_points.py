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


# ── split parts (2026-11-23): "the points still are not attaching to the time
# entry". A dtd split's 分 are a part on the entry; the close adds to them. ──

def test_a_split_part_shows_on_the_entry_and_the_close_adds_to_it(store):
    ep.record_part(DAY, "7", "PH1", 100, "split")
    assert ep.for_day(DAY) == {"7": 100}
    ep.record(DAY, {"7": 420}, "did-fast")          # the close writes its own points
    assert ep.for_day(DAY) == {"7": 520}
    assert ep.base_for_day(DAY) == {"7": 420}


def test_a_part_alone_is_not_a_completion(store):
    ep.record_part(DAY, "7", "PH1", 100)
    assert ep.base_for_day(DAY) == {} and ep.logged_for_day(DAY) == {}
    assert ep.overlay({}, DAY) == {"7": 100}


def test_undoing_the_close_leaves_the_split_and_undoing_the_split_leaves_the_close(store):
    ep.record_part(DAY, "7", "PH1", 100)
    ep.record(DAY, {"7": 420})
    ep.record(DAY, {"7": None}, "undo")
    assert ep.for_day(DAY) == {"7": 100}
    ep.record(DAY, {"7": 420})
    ep.record_part(DAY, "7", "PH1", None, "undo split")
    assert ep.for_day(DAY) == {"7": 420}
    ep.record(DAY, {"7": None})
    assert ep.for_day(DAY) == {"7": None}           # nothing left: a plain tombstone


def test_two_splits_of_one_entry_sum_and_one_key_never_counts_twice(store):
    ep.record_part(DAY, "7", "PH1", 100)
    ep.record_part(DAY, "7", "PH1", 100)            # a retried write
    ep.record_part(DAY, "7", "PH2", 30)
    assert ep.for_day(DAY) == {"7": 130} and ep.parts_sum(DAY, ["7", "8"]) == 130


def test_a_whole_total_writer_subtracts_the_parts(store):
    # ^G / pin / mobile adjust hold the shown total; a clock-skewed host must
    # not be able to resurrect a part on top of it, so parts are never cleared.
    ep.record_part(DAY, "7", "PH1", 100)
    ep.record(DAY, {"7": 520})
    ep.record(DAY, {"7": 600 - ep.parts_sum(DAY, ["7"])}, "janus pin")
    assert ep.for_day(DAY) == {"7": 600}


def test_parts_follow_a_recreated_entry(store):
    ep.record_part(DAY, "7", "PH1", 100)
    assert ep.move_parts(DAY, ["7"], "9") == 100
    ep.record(DAY, {"9": 420})
    assert ep.for_day(DAY) == {"9": 520}
    assert ep.move_parts(DAY, ["9"], "9") == 0


def test_a_close_with_no_time_entry_does_not_land_on_the_splits_entry(store):
    # JM 2026-11-23: split [1] in the entry, then closed the rest [2] with no
    # timer: "1分 associated with the time entry, then 2分 credited without".
    import datetime as dt
    ep.record_part(DAY, "7", "PH1", 100)
    ep.record_credit("T@" + DAY, ["write memo"], 25, DAY, DAY)
    start = dt.datetime.fromisoformat(DAY + "T09:00:00")
    at = dt.datetime.fromtimestamp(__import__("time").time() + 5).isoformat()
    got = ep.link([{"id": "7", "desc": "write memo", "start": start, "at": at}], today=DAY)
    assert got == {} and ep.for_day(DAY) == {"7": 100}


RUN = {"id": 50, "description": "Allergies -> Checkin (82) [520]", "start": DAY + "T12:54:00-08:00",
       "duration": -1}
OLD = {"id": 40, "description": "allergies -> checkin", "start": DAY + "T09:00:00-08:00"}
NAMES = ["Allergies -> Checkin"]


def test_pick_the_running_timer_started_for_the_task():
    assert ep.pick_entry(["other name"], "T1", RUN, [], lambda i: "T1") is RUN


def test_a_timer_linked_to_another_task_is_never_picked_by_name():
    assert ep.pick_entry(NAMES, "T1", RUN, [], lambda i: "T2") is None
    assert ep.pick_entry(NAMES, "T1", RUN, [OLD], lambda i: "T2") is OLD


def test_an_unlinked_running_timer_matches_by_name_else_the_latest_entry_today():
    assert ep.pick_entry(NAMES, "T1", RUN, [], lambda i: None) is RUN
    other = dict(RUN, description="vibing")
    later = dict(OLD, id=41, start=DAY + "T11:00:00-08:00")
    assert ep.pick_entry(NAMES, "T1", other, [OLD, later, other], None) is later
    assert ep.pick_entry(NAMES, "T1", other, [other], None) is None


def test_credit_part_records_on_the_entrys_own_day_and_reports_it(store):
    got = ep.credit_part(NAMES, 100, "PH1", task_id="T1",
                         toggl=lambda: (RUN, lambda: []), task_for_entry=lambda i: "T1")
    assert got == {"day": DAY, "id": "50", "pts": 100, "key": "PH1"}
    assert ep.for_day(DAY) == {"50": 100}


def test_credit_part_with_no_matching_entry_records_nothing(store):
    assert ep.credit_part(NAMES, 100, "PH1", toggl=lambda: (None, lambda: []),
                          task_for_entry=lambda i: None) is None
    assert ep.for_day(DAY) == {}


def test_credit_part_never_raises(store):
    def boom():
        raise RuntimeError("toggl down")
    assert ep.credit_part(NAMES, 100, "PH1", toggl=boom) is None
