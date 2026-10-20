"""2026-10-17: fixes read off the first week of janus-events.tsv — auto 睡觉,
project-code typo guard, +N riding on the last entry, ^S→/done on a long i9
timer, untracked-since in the pinned bar. Pure-function tests."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load():
    spec = importlib.util.spec_from_file_location("janus_logfix", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_logfix"] = mod
    spec.loader.exec_module(mod)
    return mod


MOD = _load()


def _e(desc, sh, sm, eh, em, eid=1, day=None, running=False):
    base = (day or dtm.datetime.now(TZ)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"desc": desc, "start_dt": base.replace(hour=sh, minute=sm),
            "end_dt": base.replace(hour=eh, minute=em), "id": eid, "running": running,
            "project_id": None, "tags": []}


NOW = dtm.datetime.now(TZ).replace(hour=7, minute=30, second=0, microsecond=0)
MID = NOW.replace(hour=0, minute=0)


# ── auto 睡觉 ──────────────────────────────────────────────────────────────
def test_auto_sleep_creates_0000_0600_on_an_empty_morning():
    kind, p = MOD._auto_sleep_plan([], [], NOW.replace(hour=0, minute=10), {})
    assert kind == "create" and p["start"] == MID and p["end"] == MID.replace(hour=6)


def test_auto_sleep_skips_when_yesterday_still_runs_or_day_has_entries():
    assert MOD._auto_sleep_plan([], [_e("late", 22, 0, 23, 59, running=True)], NOW, {}) is None
    assert MOD._auto_sleep_plan([_e("x", 0, 30, 1, 0)], [], NOW, {}) is None
    assert MOD._auto_sleep_plan([], [], NOW.replace(hour=13), {}) is None  # past noon: not a sleep question


def test_auto_sleep_extends_to_a_later_first_entry():
    st = {"date": NOW.date().isoformat(), "id": 9, "adjusted": False}
    ents = [_e("睡觉", 0, 0, 6, 0, eid=9), _e("theo lego", 7, 3, 7, 40, eid=10)]
    kind, p = MOD._auto_sleep_plan(ents, [], NOW.replace(hour=8), st)
    assert kind == "adjust" and p["end"] == MID.replace(hour=7, minute=3) and p["id"] == 9


def test_auto_sleep_shrinks_to_an_earlier_first_entry():
    st = {"date": NOW.date().isoformat(), "id": 9, "adjusted": False}
    ents = [_e("睡觉", 0, 0, 6, 0, eid=9), _e("wake up", 5, 40, 5, 50, eid=10)]
    kind, p = MOD._auto_sleep_plan(ents, [], NOW, st)
    assert kind == "adjust" and p["end"] == MID.replace(hour=5, minute=40)


def test_auto_sleep_deletes_when_a_hand_typed_sleep_or_midnight_entry_exists():
    st = {"date": NOW.date().isoformat(), "id": 9, "adjusted": False}
    ents = [_e("睡觉", 0, 0, 6, 0, eid=9), _e("睡觉", 0, 0, 5, 40, eid=11)]
    assert MOD._auto_sleep_plan(ents, [], NOW, st)[0] == "delete"
    ents = [_e("睡觉", 0, 0, 6, 0, eid=9), _e("late night", 0, 0, 1, 0, eid=12)]
    assert MOD._auto_sleep_plan(ents, [], NOW, st)[0] == "delete"


def test_auto_sleep_settles_past_cap_retimed_gone_or_aligned_and_only_once():
    st = {"date": NOW.date().isoformat(), "id": 9, "adjusted": False}
    late = [_e("睡觉", 0, 0, 6, 0, eid=9), _e("x", 10, 30, 11, 0, eid=10)]
    assert MOD._auto_sleep_plan(late, [], NOW.replace(hour=11), st)[0] == "settle"
    assert MOD._auto_sleep_plan([_e("睡觉", 0, 30, 6, 0, eid=9), _e("x", 7, 0, 8, 0, eid=10)], [], NOW, st)[0] == "settle"
    assert MOD._auto_sleep_plan([_e("x", 7, 0, 8, 0, eid=10)], [], NOW, st)[0] == "settle"
    assert MOD._auto_sleep_plan([_e("睡觉", 0, 0, 6, 0, eid=9), _e("x", 6, 0, 8, 0, eid=10)], [], NOW, st)[0] == "settle"
    assert MOD._auto_sleep_plan([_e("睡觉", 0, 0, 6, 0, eid=9), _e("x", 7, 0, 8, 0, eid=10)], [], NOW, {**st, "adjusted": True}) is None
    # only 睡觉 so far: keep waiting
    assert MOD._auto_sleep_plan([_e("睡觉", 0, 0, 6, 0, eid=9)], [], NOW, st) is None


def test_auto_sleep_state_from_another_day_is_ignored():
    st = {"date": "2000-01-01", "id": 9, "adjusted": True}
    assert MOD._auto_sleep_plan([], [], NOW, st)[0] == "create"


def test_auto_sleep_host_gate(monkeypatch):
    monkeypatch.setenv("JANUS_AUTO_SLEEP", "1"); assert MOD._auto_sleep_enabled()
    monkeypatch.setenv("JANUS_AUTO_SLEEP", "0"); assert not MOD._auto_sleep_enabled()


# ── project-code typo guard ───────────────────────────────────────────────
KNOWN = {"i9", "m5x2", "hcmc", "hcmc2", "xk87", "xk88", "睡觉", "g245", "qz12", "hcb"}


def test_fix_project_codes_corrects_near_misses_and_reports_unfixable():
    out, fixes, bad = MOD._fix_project_codes("0640-0648 vibing @9", KNOWN)
    assert out == "0640-0648 vibing @i9" and fixes == [("9", "i9")] and bad == []
    out, fixes, bad = MOD._fix_project_codes("1055-1105 call HZ @m5x", KNOWN)
    assert out.endswith("@m5x2") and fixes == [("m5x", "m5x2")]
    out, fixes, bad = MOD._fix_project_codes("coffee @zzzz", KNOWN)
    assert out == "coffee @zzzz" and fixes == [] and bad == ["zzzz"]


def test_fix_project_codes_leaves_known_codes_and_emails_alone():
    assert MOD._fix_project_codes("+15 @i9 XTECH", KNOWN) == ("+15 @i9 XTECH", [], [])
    assert MOD._fix_project_codes("mail jm@m5c7.com @i9", KNOWN)[0] == "mail jm@m5c7.com @i9"
    assert MOD._fix_project_codes("x @9", set()) == ("x @9", [], [])  # no table: untouched


# ── +N rides on the last entry ────────────────────────────────────────────
def test_bonus_has_note():
    assert not MOD._bonus_has_note("+15 @i9")
    assert not MOD._bonus_has_note("-10 @g245 10/16")
    assert MOD._bonus_has_note("-8 0g")
    assert MOD._bonus_has_note("+50 understand WoW ROAS @i9")
    assert MOD._bonus_has_note("+15 个")


def test_last_entry_desc_prefers_running_then_latest_finished_non_sleep():
    MOD.STATE.current = {"description": "XTECH [45] @i9", "project_id": None}
    assert MOD._last_entry_desc() == "XTECH"
    MOD.STATE.current = None
    MOD.STATE.entries = [_e("睡觉", 0, 0, 6, 0, eid=1), _e("tasks", 6, 0, 6, 30, eid=2),
                         _e("Bei 1:1", 11, 0, 11, 34, eid=3), _e("live", 12, 0, 12, 5, eid=4, running=True)]
    assert MOD._last_entry_desc() == "Bei 1:1"
    MOD.STATE.entries = [_e("睡觉", 0, 0, 6, 0, eid=1)]
    assert MOD._last_entry_desc() == ""


# ── ^S on a long i9 timer ─────────────────────────────────────────────────
def test_stop_wants_points_only_for_long_unrecorded_i9(monkeypatch):
    monkeypatch.setattr(MOD, "toggl_project_code", lambda pid, desc=None: "i9" if pid == 1 else "hcb")
    monkeypatch.setattr(MOD, "_points_recorded_today", lambda desc: (desc == "done one", 10))
    start = (NOW - dtm.timedelta(minutes=25)).isoformat()
    assert MOD._stop_wants_points({"start": start, "project_id": 1, "description": "XTECH"}, NOW)
    assert not MOD._stop_wants_points({"start": start, "project_id": 2, "description": "run"}, NOW)
    assert not MOD._stop_wants_points({"start": (NOW - dtm.timedelta(minutes=5)).isoformat(),
                                       "project_id": 1, "description": "XTECH"}, NOW)
    assert not MOD._stop_wants_points({"start": start, "project_id": 1, "description": "done one"}, NOW)
    assert not MOD._stop_wants_points({"start": "garbage", "project_id": 1, "description": "x"}, NOW)


# ── untracked since ───────────────────────────────────────────────────────
def test_untracked_since_reads_the_gap_after_the_last_completed_entry():
    MOD.STATE.day_offset = 0; MOD.STATE.current = None; MOD.STATE.entries_known = True
    MOD.STATE.entries = [_e("睡觉", 0, 0, 6, 0, eid=1), _e("tasks", 6, 0, 6, 31, eid=2)]
    assert MOD._untracked_since(NOW) == MID.replace(hour=6, minute=31)          # 59m gap
    assert MOD._untracked_since(MID.replace(hour=6, minute=50)) is None         # 19m: under the bar
    MOD.STATE.entries = [_e("睡觉", 0, 0, 6, 0, eid=1)]
    assert MOD._untracked_since(MID.replace(hour=3)) is None                    # auto-睡觉 ends in the future
    MOD.STATE.entries = []
    assert MOD._untracked_since(NOW) is None                                    # empty day: not a nag
    MOD.STATE.entries = [_e("tasks", 6, 0, 6, 31, eid=2)]
    MOD.STATE.current = {"id": 5}
    assert MOD._untracked_since(NOW) is None                                    # a timer runs
    MOD.STATE.current = None; MOD.STATE.day_offset = 1
    assert MOD._untracked_since(NOW) is None                                    # past-day view
    MOD.STATE.day_offset = 0


def test_ride_bonus_note_only_for_bare_signed_grants_with_a_code():
    MOD.STATE.current = {"description": "XTECH", "project_id": None}
    assert MOD._ride_bonus_note("+15 @i9") == "+15 @i9 XTECH"
    assert MOD._ride_bonus_note("-10 @g245") == "-10 @g245 XTECH"
    assert MOD._ride_bonus_note("+15 @i9 read COD deck") == "+15 @i9 read COD deck"
    assert MOD._ride_bonus_note("+15 个") == "+15 个"
    assert MOD._ride_bonus_note("+15") == "+15"                      # no @code: domain would come from the note
    assert MOD._ride_bonus_note("0915-1000 XTECH @i9 [45]") == "0915-1000 XTECH @i9 [45]"
    MOD.STATE.current = None; MOD.STATE.entries = []
    assert MOD._ride_bonus_note("+15 @i9") == "+15 @i9"             # nothing to ride on
