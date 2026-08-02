"""User request 2026-08-02: "hitting 'enter' on a meeting will also kick off
a d357 recording session. It should also add an emoji to the current meeting
so I know it worked. When I hit opt enter, or enter on another meeting from
Janus, that means the original one is over so we can close the meeting
finalize the notes, and record the points."""
import asyncio
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_rec", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_rec"] = mod
    spec.loader.exec_module(mod)
    return mod


class _App:
    def create_background_task(self, coro):
        return asyncio.get_event_loop().create_task(coro)

    def invalidate(self):
        pass


def _reset(mod):
    mod.STATE.work_q = None
    mod.STATE.queued_cmds = set()
    mod.STATE.conversion_in_flight = False
    mod.STATE.recording = None
    mod.STATE.entries = []
    mod.STATE.entries_yday = []
    mod.STATE.day_offset = 0


def _now():
    return dtm.datetime.now(TZ)


def _live_event(mod, title, started_min_ago=4, remaining_min=26):
    now = _now()
    return {"title": title, "start_dt": now - dtm.timedelta(minutes=started_min_ago),
            "end_dt": now + dtm.timedelta(minutes=remaining_min),
            "calendar": "Outlook", "all_day": False, "transparency": "opaque"}


def _stub_subprocess(mod, monkeypatch, rec_lines):
    calls = []

    def fake_run(args, **kw):
        calls.append(("run", args))

        class R:
            returncode = 0
            stdout = rec_lines
            stderr = ""
        return R()

    def fake_popen(args, **kw):
        calls.append(("popen", args))

        class P:
            pid = 999
        return P()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    return calls


def test_enter_on_live_meeting_starts_recording(monkeypatch):
    mod = _load_tui()
    _reset(mod)
    calls = _stub_subprocess(mod, monkeypatch, "REC|Huddle: XBOX Developer|ok channels=both")
    monkeypatch.setattr(mod, "run_tg_fast", lambda cmd: "Started")
    monkeypatch.setattr(mod, "fetch_current", lambda *a: None)
    monkeypatch.setattr(mod, "fetch_today", lambda *a: None)

    async def main():
        app = _App()
        mod._convert_selected_event(_live_event(mod, "Huddle: XBOX Developer"), app)
        await mod.STATE.work_q.join()

    asyncio.run(main())
    started = [a for k, a in calls if k == "run" and mod.D357_QUICK in a]
    assert started and started[0][2] == "start" and started[0][3] == "Huddle: XBOX Developer"
    assert mod.STATE.recording and mod.STATE.recording["desc"] == "Huddle: XBOX Developer"
    assert "🎙" in mod.STATE.flash


def test_enter_on_second_meeting_finalizes_first(monkeypatch):
    """Enter on another meeting = the original is over: d357 stop is spawned,
    the old meeting's did-fast range command grants its points, and the
    recording switches to the new meeting."""
    mod = _load_tui()
    _reset(mod)
    calls = _stub_subprocess(mod, monkeypatch, "REC|Growth Weekly|ok channels=both")
    did_cmds = []
    monkeypatch.setattr(mod, "run_tg_fast", lambda cmd: "Started")
    monkeypatch.setattr(mod, "run_did_fast", lambda cmd: did_cmds.append(cmd) or "did ok")
    monkeypatch.setattr(mod, "fetch_current", lambda *a: None)
    monkeypatch.setattr(mod, "fetch_today", lambda *a: None)
    monkeypatch.setattr(mod, "fetch_points", lambda *a: None)
    start = _now() - dtm.timedelta(minutes=30)
    mod.STATE.recording = {"desc": "Huddle: XBOX Developer", "start_dt": start}
    mod.STATE.entries = [{"id": 7, "desc": "Huddle: XBOX Developer", "project_id": 9,
                          "running": True, "start_dt": start,
                          "end_dt": _now(), "tags": []}]
    monkeypatch.setattr(mod, "proj_code", lambda pid: "i9" if pid == 9 else "")

    async def main():
        app = _App()
        mod._convert_selected_event(_live_event(mod, "Growth Weekly"), app)
        await mod.STATE.work_q.join()

    asyncio.run(main())
    stops = [a for k, a in calls if k == "popen" and mod.D357_QUICK in a and "stop" in a]
    assert stops, "the previous recording must be stopped (detached)"
    assert did_cmds and did_cmds[0].startswith("Huddle: XBOX Developer ")
    assert "@i9" in did_cmds[0], "old meeting's points ride its entry's project"
    assert mod.STATE.recording["desc"] == "Growth Weekly"


def test_alt_enter_finalizes_active_recording(monkeypatch):
    mod = _load_tui()
    _reset(mod)
    calls = _stub_subprocess(mod, monkeypatch, "")
    did_cmds = []
    monkeypatch.setattr(mod, "run_did_fast", lambda cmd: did_cmds.append(cmd) or "did ok")
    monkeypatch.setattr(mod, "fetch_today", lambda *a: None)
    monkeypatch.setattr(mod, "fetch_points", lambda *a: None)
    start = _now() - dtm.timedelta(minutes=25)
    mod.STATE.recording = {"desc": "Huddle: XBOX Developer", "start_dt": start}
    mod.STATE.visible_events = []
    mod.STATE.event_sel = None
    mod.input_buffer.text = ""
    binding = next(b for b in mod.kb.bindings if b.keys == ("escape", "c-m"))

    class _Ev:
        app = _App()

    async def main():
        binding.handler(_Ev())
        await mod.STATE.work_q.join()

    asyncio.run(main())
    stops = [a for k, a in calls if k == "popen" and mod.D357_QUICK in a]
    assert stops, "⌥↵ must stop the live recording"
    assert did_cmds and did_cmds[0].startswith("Huddle: XBOX Developer ")
    assert mod.STATE.recording is None


def test_rec_indicator_on_running_row():
    mod = _load_tui()
    _reset(mod)
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    pick = {"start_dt": today.replace(hour=12, minute=10), "time_str": "12:10",
            "label": "Huddle: XBOX Developer", "style": "", "dur_min": 5,
            "entry_ids": [1], "raw_desc": "Huddle: XBOX Developer",
            "project_id": None, "is_running": True, "tags": []}
    mod.STATE.recording = {"desc": "Huddle: XBOX Developer", "start_dt": today}
    text = "".join(t for _, t in mod._compact_block_lines("未", 12, [pick], 0, ""))
    line = next(l for l in text.split("\n") if "🎙" in l)
    assert line.index("🎙") > line.index("Huddle"), \
        "🎙 sits to the RIGHT of the task name (user request 2026-08-02)"
    assert "▶🎙" not in line, "no longer fused to the ▶ marker"
    mod.STATE.recording = None
    text = "".join(t for _, t in mod._compact_block_lines("未", 12, [pick], 0, ""))
    assert "🎙" not in text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
