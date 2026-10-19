"""Stale overnight timer → 睡觉 (2026-10-19).

10/19: a -1g timer started 20:51 ran until the 05:54 economist; the
empty-morning auto-睡觉 never fires because a running timer (and later the
split 0000-0554 -1g) always disqualifies the day. Rule: a yesterday entry
still running after midnight that started before 22:00 is cut at 22:00 and
睡觉 22:00-23:59 + 00:00-05:30 are created; the 00:00 piece then follows the
normal first-entry retime.
"""
import datetime as dtm
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load():
    spec = importlib.util.spec_from_file_location("janus_stale_mod", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_stale_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


MOD = _load()

NOW = dtm.datetime.now(TZ).replace(hour=1, minute=30, second=0, microsecond=0)
MID = NOW.replace(hour=0, minute=0)
YDAY = MID - dtm.timedelta(days=1)


def _e(desc, sh, sm, eh, em, eid=1, day=None, running=False):
    base = (day or MID)
    return {"desc": desc, "start_dt": base.replace(hour=sh, minute=sm),
            "end_dt": base.replace(hour=eh, minute=em), "id": eid, "running": running,
            "project_id": None, "tags": []}


def _y(desc, sh, sm, eh=23, em=59, eid=1, running=False):
    return _e(desc, sh, sm, eh, em, eid=eid, day=YDAY, running=running)


# ── planner ────────────────────────────────────────────────────────────────
def test_stale_minus1g_from_2051_is_cut_at_2200_with_two_sleep_pieces():
    kind, p = MOD._auto_sleep_plan([], [_y("-1g", 20, 51, running=True, eid=77)], NOW, {})
    assert kind == "stale"
    assert p["id"] == 77 and p["desc"] == "-1g"
    assert p["cut"] == YDAY.replace(hour=22, minute=0)
    assert p["y_start"] == YDAY.replace(hour=22, minute=0) and p["y_end"] == YDAY.replace(hour=23, minute=59)
    assert p["t_start"] == MID and p["t_end"] == MID.replace(hour=5, minute=30)


def test_stale_ignores_a_running_sleep_timer_and_timers_started_after_2200():
    assert MOD._auto_sleep_plan([], [_y("睡觉", 21, 30, running=True)], NOW, {}) is None
    assert MOD._auto_sleep_plan([], [_y("movie", 22, 0, running=True)], NOW, {}) is None
    assert MOD._auto_sleep_plan([], [_y("movie", 22, 30, running=True)], NOW, {}) is None


def test_stale_waits_until_0100_and_stops_judging_at_noon():
    run = [_y("-1g", 20, 51, running=True)]
    assert MOD._auto_sleep_plan([], run, NOW.replace(hour=0, minute=40), {}) is None
    assert MOD._auto_sleep_plan([], run, NOW.replace(hour=1, minute=0), {})[0] == "stale"
    assert MOD._auto_sleep_plan([], run, NOW.replace(hour=11, minute=59), {})[0] == "stale"
    assert MOD._auto_sleep_plan([], run, NOW.replace(hour=12, minute=0), {}) is None


def test_stale_needs_an_empty_today():
    run = [_y("-1g", 20, 51, running=True)]
    assert MOD._auto_sleep_plan([_e("water", 0, 30, 0, 35, eid=5)], run, NOW, {}) is None


def test_stale_cut_moves_past_a_hand_logged_evening_entry():
    yd = [_y("-1g", 20, 51, running=True, eid=1), _y("bath kids", 22, 10, 23, 0, eid=2)]
    kind, p = MOD._auto_sleep_plan([], yd, NOW, {})
    assert kind == "stale" and p["cut"] == YDAY.replace(hour=23, minute=0) == p["y_start"]


def test_stale_skips_the_yesterday_piece_when_it_would_be_a_sliver():
    yd = [_y("-1g", 20, 51, running=True, eid=1), _y("late", 22, 10, 23, 57, eid=2)]
    kind, p = MOD._auto_sleep_plan([], yd, NOW, {})
    assert kind == "stale" and p["y_start"] is None and p["cut"] == YDAY.replace(hour=23, minute=57)


def test_stale_state_is_resumed_and_settled_state_blocks_it():
    pending = {"date": NOW.date().isoformat(), "phase": "stale", "plan": {}, "done": {}}
    assert MOD._auto_sleep_plan([], [], NOW, pending) == ("stale", pending)
    settled = {"date": NOW.date().isoformat(), "id": 9, "adjusted": True}
    assert MOD._auto_sleep_plan([], [_y("-1g", 20, 51, running=True)], NOW, settled) is None


def test_empty_morning_create_path_is_unchanged():
    kind, p = MOD._auto_sleep_plan([], [], NOW.replace(hour=0, minute=10), {})
    assert kind == "create" and p["end"] == MID.replace(hour=6)


# ── apply (three Toggl writes, check-pointed) ──────────────────────────────
class _FakeToggl:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on, self._next = [], fail_on, 500

    def update_entry(self, eid, **f):
        self.calls.append(("update", eid, f))
        if self.fail_on == "update":
            raise RuntimeError("boom")

    def create_entry(self, desc, start, stop, dur, pid, tags):
        self.calls.append(("create", desc, start, stop, dur))
        if self.fail_on == f"create{len([c for c in self.calls if c[0] == 'create'])}":
            raise RuntimeError("boom")
        self._next += 1
        return {"id": self._next}


def _wire(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(MOD, "AUTO_SLEEP_STATE", tmp_path / "auto-sleep.json")
    monkeypatch.setattr(MOD, "toggl_api", fake)
    monkeypatch.setattr(MOD.JE, "emit", lambda *a, **k: None)
    monkeypatch.setattr(MOD, "PROJECT_MAP", {"睡觉": 108358083}, raising=False)


def test_apply_stale_cuts_then_creates_both_pieces_and_leaves_adjustable_state(monkeypatch, tmp_path):
    fake = _FakeToggl()
    _wire(monkeypatch, tmp_path, fake)
    _, plan = MOD._auto_sleep_plan([], [_y("-1g", 20, 51, running=True, eid=77)], NOW, {})
    final = MOD._auto_sleep_apply_stale(plan, NOW.date().isoformat())
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["update", "create", "create"]
    assert fake.calls[0][1] == 77 and fake.calls[0][2]["stop"] == YDAY.replace(hour=22).isoformat()
    assert fake.calls[0][2]["duration"] == 69 * 60
    assert fake.calls[1][1] == "睡觉" and fake.calls[1][4] == 119 * 60          # 22:00-23:59
    assert fake.calls[2][1] == "睡觉" and fake.calls[2][4] == 330 * 60          # 00:00-05:30
    assert final == {"date": NOW.date().isoformat(), "id": 502, "adjusted": False}
    assert json.loads((tmp_path / "auto-sleep.json").read_text()) == final


def test_apply_stale_resumes_after_a_crash_without_repeating_done_steps(monkeypatch, tmp_path):
    fake = _FakeToggl(fail_on="create1")
    _wire(monkeypatch, tmp_path, fake)
    _, plan = MOD._auto_sleep_plan([], [_y("-1g", 20, 51, running=True, eid=77)], NOW, {})
    try:
        MOD._auto_sleep_apply_stale(plan, NOW.date().isoformat())
    except RuntimeError:
        pass
    st = json.loads((tmp_path / "auto-sleep.json").read_text())
    assert st["phase"] == "stale" and st["done"] == {"cut": True}
    # next tick: planner hands the pending state back; apply skips the cut
    kind, p = MOD._auto_sleep_plan([], [], NOW.replace(minute=45), st)
    assert kind == "stale"
    fake.fail_on = None
    final = MOD._auto_sleep_apply_stale(p, NOW.date().isoformat())
    assert [c[0] for c in fake.calls] == ["update", "create", "create", "create"]
    assert final["id"] == 502 and final["adjusted"] is False


def test_the_morning_retime_still_applies_to_the_stale_sleep_piece():
    st = {"date": NOW.date().isoformat(), "id": 502, "adjusted": False}
    ents = [_e("睡觉", 0, 0, 5, 30, eid=502), _e("economist", 5, 54, 6, 1, eid=600)]
    kind, p = MOD._auto_sleep_plan(ents, [], NOW.replace(hour=6), st)
    assert kind == "adjust" and p["id"] == 502 and p["end"] == MID.replace(hour=5, minute=54)
