#!/usr/bin/env python3
"""Tests for night_hcmc_toggl.py's pure planning logic (no live Toggl calls)."""
import datetime
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import night_hcmc_toggl as nh  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")
DAY = datetime.date(2026, 8, 18)


def _iso(hour, minute, day_offset=0):
    d = DAY + datetime.timedelta(days=day_offset)
    return datetime.datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ).isoformat()


def _sleep_entry(start_h, start_m, end_h, end_m, end_day_offset=1, entry_id=1):
    return {"id": entry_id, "project_id": nh.SLEEP_PROJECT_ID, "description": "睡觉",
            "start": _iso(start_h, start_m), "stop": _iso(end_h, end_m, end_day_offset)}


def _placeholder_entry(start_h, start_m, end_h, end_m, end_day_offset=1, entry_id=2):
    return {"id": entry_id, "project_id": nh.INFRA_PROJECT_ID, "description": "generic placeholder",
            "start": _iso(start_h, start_m), "stop": _iso(end_h, end_m, end_day_offset)}


# ── find_candidate ────────────────────────────────────────────────────────

def test_finds_sleep_entry_crossing_midnight():
    e = _sleep_entry(22, 30, 7, 0)
    candidate, err = nh.find_candidate([e], DAY)
    assert err is None
    assert candidate["id"] == 1


def test_finds_generic_placeholder_crossing_midnight():
    e = _placeholder_entry(22, 30, 7, 0)
    candidate, err = nh.find_candidate([e], DAY)
    assert err is None
    assert candidate["id"] == 2


def test_ignores_entry_before_window():
    # starts 19:59 -- one minute before the 20:00 window opens
    e = _sleep_entry(19, 59, 7, 0)
    candidate, err = nh.find_candidate([e], DAY)
    assert candidate is None
    assert err == "no_candidate"


def test_ignores_entry_after_window():
    # starts 23:00 -- window is exclusive at the top
    e = _sleep_entry(23, 0, 7, 0)
    candidate, err = nh.find_candidate([e], DAY)
    assert candidate is None
    assert err == "no_candidate"


def test_ignores_entry_not_crossing_midnight():
    # an evening nap that ends same-day, not an overnight block
    e = _sleep_entry(20, 30, 21, 30, end_day_offset=0)
    candidate, err = nh.find_candidate([e], DAY)
    assert candidate is None
    assert err == "no_candidate"


def test_ignores_still_running_entry():
    e = _sleep_entry(22, 0, 0, 0)
    e["stop"] = None
    candidate, err = nh.find_candidate([e], DAY)
    assert candidate is None
    assert err == "no_candidate"


def test_ignores_placeholder_with_wrong_description():
    e = _placeholder_entry(22, 0, 7, 0)
    e["description"] = "unrelated thing"
    candidate, err = nh.find_candidate([e], DAY)
    assert candidate is None
    assert err == "no_candidate"


def test_ambiguous_when_two_candidates_match():
    e1 = _sleep_entry(20, 30, 6, 0, entry_id=1)
    e2 = _placeholder_entry(22, 0, 7, 0, entry_id=2)
    candidate, err = nh.find_candidate([e1, e2], DAY)
    assert candidate is None
    assert err == "ambiguous"


def test_ignores_entries_from_other_days():
    e = _sleep_entry(22, 0, 7, 0)
    other_day = DAY - datetime.timedelta(days=3)
    candidate, err = nh.find_candidate([e], other_day)
    assert candidate is None
    assert err == "no_candidate"


# ── plan_placement: normal case (matches the user's spec exactly) ─────────

def test_plan_normal_case_produces_three_segments():
    e = _sleep_entry(22, 30, 7, 0)  # 22:30 -> next day 07:00
    plan = nh.plan_placement([e], DAY, minutes=45)
    assert plan["ok"] is True
    segs = plan["segments"]
    assert len(segs) == 3

    desc0, proj0, start0, end0 = segs[0]
    assert desc0 == "night hcmc" and proj0 == nh.HCMC_PROJECT_ID
    assert start0 == datetime.datetime(2026, 8, 18, 22, 30, tzinfo=TZ)
    assert end0 == datetime.datetime(2026, 8, 18, 23, 15, tzinfo=TZ)

    desc1, proj1, start1, end1 = segs[1]
    assert desc1 == "睡觉" and proj1 == nh.SLEEP_PROJECT_ID
    assert start1 == end0
    assert end1 == datetime.datetime(2026, 8, 19, 0, 0, tzinfo=TZ)  # midnight

    desc2, proj2, start2, end2 = segs[2]
    assert desc2 == "睡觉" and proj2 == nh.SLEEP_PROJECT_ID
    assert start2 == end1
    assert end2 == datetime.datetime(2026, 8, 19, 7, 0, tzinfo=TZ)


def test_plan_relabels_generic_placeholder_remainder_as_sleep():
    e = _placeholder_entry(21, 0, 6, 30)
    plan = nh.plan_placement([e], DAY, minutes=20)
    assert plan["ok"] is True
    # every segment after the hcmc one must be labeled 睡觉, never "generic placeholder"
    for desc, proj, _, _ in plan["segments"][1:]:
        assert desc == "睡觉"
        assert proj == nh.SLEEP_PROJECT_ID


def test_plan_no_candidate_reports_reason():
    plan = nh.plan_placement([], DAY, minutes=30)
    assert plan["ok"] is False
    assert plan["reason"] == "no_candidate"


def test_plan_bails_when_minutes_exceeds_entry_duration():
    # 90-min entry crossing midnight: 22:45 -> 00:15
    e = _sleep_entry(22, 45, 0, 15)
    plan = nh.plan_placement([e], DAY, minutes=95)  # more than the 90-min entry
    assert plan["ok"] is False
    assert "duration" in plan["reason"]


def test_plan_zero_minutes_rejected():
    e = _sleep_entry(22, 0, 7, 0)
    plan = nh.plan_placement([e], DAY, minutes=0)
    assert plan["ok"] is False


# ── plan_placement: hcmc listening itself crosses midnight ────────────────

def test_plan_handles_hcmc_crossing_midnight_itself():
    # starts 22:50, and 90 minutes of listening pushes past midnight (00:20)
    e = _sleep_entry(22, 50, 8, 0)
    plan = nh.plan_placement([e], DAY, minutes=90)
    assert plan["ok"] is True
    segs = plan["segments"]
    # Expect: hcmc [22:50-00:00], hcmc [00:00-00:20], sleep [00:20-08:00]
    assert len(segs) == 3
    assert segs[0][0] == "night hcmc"
    assert segs[0][2] == datetime.datetime(2026, 8, 18, 22, 50, tzinfo=TZ)
    assert segs[0][3] == datetime.datetime(2026, 8, 19, 0, 0, tzinfo=TZ)
    assert segs[1][0] == "night hcmc"
    assert segs[1][2] == datetime.datetime(2026, 8, 19, 0, 0, tzinfo=TZ)
    assert segs[1][3] == datetime.datetime(2026, 8, 19, 0, 20, tzinfo=TZ)
    assert segs[2][0] == "睡觉"
    assert segs[2][2] == datetime.datetime(2026, 8, 19, 0, 20, tzinfo=TZ)
    assert segs[2][3] == datetime.datetime(2026, 8, 19, 8, 0, tzinfo=TZ)


def test_plan_hcmc_ending_exactly_at_midnight_has_no_zero_length_segment():
    e = _sleep_entry(22, 0, 7, 0)
    plan = nh.plan_placement([e], DAY, minutes=120)  # 22:00 + 120min = exactly midnight
    assert plan["ok"] is True
    segs = plan["segments"]
    assert len(segs) == 2  # one hcmc segment, one sleep segment -- no zero-length middle segment
    assert segs[0][0] == "night hcmc"
    assert segs[0][3] == datetime.datetime(2026, 8, 19, 0, 0, tzinfo=TZ)
    assert segs[1][0] == "睡觉"
    assert segs[1][2] == datetime.datetime(2026, 8, 19, 0, 0, tzinfo=TZ)


# ── wiring: did-fast.py actually calls the hook, correctly gated ──────────

DIDFAST = (Path(__file__).parent / "did-fast.py").read_text()


def test_did_fast_calls_apply_placement_for_night_hcmc():
    assert "import night_hcmc_toggl as _nh" in DIDFAST
    assert "_nh.apply_placement(" in DIDFAST


def test_did_fast_only_fires_for_night_hcmc_habit():
    assert 'r.item.name.lower() != "night hcmc"' in DIDFAST


def test_did_fast_gates_on_successful_0n_write():
    # Must not attempt Toggl placement before the Excel write is confirmed.
    assert "if on_result and on_result.returncode == 0:" in DIDFAST


def test_did_fast_never_lets_placement_fail_the_completion():
    assert "except Exception as e:  # noqa: BLE001 — best-effort, never fail the habit" in DIDFAST


def test_did_fast_attaches_result_to_the_item_entry():
    assert 'entry["night_hcmc_toggl"] = night_hcmc_results[r.item.name]' in DIDFAST


def test_did_fast_searches_the_evening_before_target_date():
    # Regression 2026-08-19: JM completes "night hcmc" the morning after,
    # attributed to TODAY (no "yesterday" suffix -- "I record it as today
    # since this is the only time I could record it"). The Neon write
    # correctly lands on target_date's row, but the sleep/placeholder block
    # to search is always the evening BEFORE that, one day earlier -- never
    # target_date's own evening (which, searched the same morning, hasn't
    # happened yet).
    assert "- timedelta(days=1))" in DIDFAST


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
