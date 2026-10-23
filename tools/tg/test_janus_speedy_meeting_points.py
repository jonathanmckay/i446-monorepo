"""Speedy meetings credit their slot (JM 2026-10-22): "for both janus CLI and
iOS, logging a 25m meeting should be marked as worth 30". Outlook's speedy
meetings end 5 min early, so a 30-min slot shows as 25 (and 60 as 50). The
CLI's did-fast commands for a MEETING of exactly 25/50 min carry an explicit
[30]/[60]; anything else (other lengths, non-meetings, entries whose dtd task
already resolves its own [N]) is untouched. Shared rule: lib/neon/rates.py."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load(tmp_path):
    spec = importlib.util.spec_from_file_location("janus_speedy", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_speedy"] = mod
    spec.loader.exec_module(mod)
    mod.TASK_QUEUE = tmp_path / "no-task-queue.json"  # isolate from the live queue
    return mod


def _t(h, m):
    return dt.datetime(2026, 10, 22, h, m, tzinfo=TZ)


def _ev(title, s, e):
    return {"title": title, "start_dt": s, "end_dt": e, "calendar": "Work"}


def test_rule_is_25_to_30_and_50_to_60_only():
    sys.path.insert(0, str(HERE.parents[1] / "lib"))
    from neon.rates import meeting_points, speedy_meeting_points
    assert speedy_meeting_points(25) == 30 and speedy_meeting_points(50) == 60
    assert [speedy_meeting_points(m) for m in (24, 26, 30, 45, 55, 60)] == [None] * 6
    assert meeting_points(25) == 30 and meeting_points(40) == 40


def test_ended_25m_event_did_command_carries_30(tmp_path):
    mod = _load(tmp_path)
    cmd = mod._event_to_did_command(_ev("Sync w/ Asha", _t(14, 5), _t(14, 30)))
    assert cmd.startswith("Sync w/ Asha 1405-1430") and cmd.endswith("[30]"), cmd


def test_ended_50m_event_carries_60_and_30m_event_unchanged(tmp_path):
    mod = _load(tmp_path)
    assert mod._event_to_did_command(_ev("Staff", _t(10, 0), _t(10, 50))).endswith("[60]")
    assert "[" not in mod._event_to_did_command(_ev("Staff", _t(10, 0), _t(10, 30)))


def test_25m_entry_is_30_only_when_it_matches_a_calendar_event(tmp_path):
    mod = _load(tmp_path)
    mod.STATE.events = [_ev("Sync w/ Asha", _t(14, 5), _t(14, 30))]
    assert mod._speedy_meeting_tag("sync w/ asha", _t(14, 5), _t(14, 30)) == " [30]"
    # Not a meeting: a 25-min habit/ad-hoc entry keeps minutes-as-points.
    assert mod._speedy_meeting_tag("新闻", _t(14, 5), _t(14, 30)) == ""
    # Same title but a different instance (no overlap) isn't this meeting.
    assert mod._speedy_meeting_tag("sync w/ asha", _t(16, 0), _t(16, 25)) == ""


def test_dtd_task_value_wins_over_meeting_rounding(tmp_path):
    mod = _load(tmp_path)
    mod.STATE.events = [_ev("odyssey prep", _t(9, 0), _t(9, 25))]
    assert mod._speedy_meeting_tag("odyssey prep [45]", _t(9, 0), _t(9, 25)) == ""
