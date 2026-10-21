"""Regression: "[N]" typed into an entry EDIT must become points, never part
of the title (bug 2026-10-21, janus CLI: selecting the running "lx chat
@xk88 0947-" and submitting "lx chat @xk88 0947-1002 [17]" renamed the
entry to "lx chat @xk88 [17]" — the bracket after the @code pushed the code
mid-string, where _parse_edit_text only strips a TRAILING one — and nothing
reached Neon).

  - closed range + [N]  → the retime lands, then the /done did-fast command
    ("desc HHMM-HHMM @code [N]") grants the 分;
  - open-ended / no range + [N] → still running, so no credit yet: the value
    is parked inline ("desc [N]") where _resolvable_points finds it on ⌥↵.
"""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load():
    spec = importlib.util.spec_from_file_location("janus_editpts", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_editpts"] = mod
    spec.loader.exec_module(mod)
    return mod


def _enter(mod):
    hits = [b for b in mod.kb.bindings if b.keys == ("c-m",)]
    assert hits
    return hits[0]


class _App:
    def create_background_task(self, coro):
        import asyncio
        asyncio.run(coro)

    def invalidate(self):
        pass


class _Event:
    app = _App()


def _arm_running(mod, monkeypatch):
    """A running "lx chat" @xk88 timer started 09:47 today, selected for edit."""
    today = dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today.replace(hour=9, minute=47)
    pid = mod.PROJECT_MAP["xk88"]
    mod.STATE.entries = [{"id": 4595608454, "desc": "lx chat", "start_dt": start,
                          "end_dt": today.replace(hour=10, minute=2), "project_id": pid,
                          "running": True, "tags": []}]
    mod.STATE.entries_yday = []
    mod.STATE.current = {"id": 4595608454, "start": start.isoformat(),
                         "description": "lx chat", "project_id": pid}
    mod.STATE.day_offset = 0
    mod.STATE.event_sel = None
    mod.STATE.done_target = None
    mod.STATE.split_target = None
    calls = []

    class _Toggl:
        def trim_range(self, start_dt, end_dt, exclude_ids=None):
            calls.append(("trim", start_dt, end_dt, exclude_ids))
            return []

        def update_entry(self, entry_id, **fields):
            calls.append(("update", entry_id, fields))

        def stop_timer(self, entry_id):
            calls.append(("stop", entry_id))

    monkeypatch.setattr(mod, "toggl_api", _Toggl())
    monkeypatch.setattr(mod, "fetch_current", lambda *a, **k: None)
    monkeypatch.setattr(mod, "fetch_today", lambda *a, **k: None)
    monkeypatch.setattr(mod, "fetch_points", lambda *a, **k: None)
    monkeypatch.setattr(mod, "run_did_fast", lambda text, flags=None: calls.append(("did", text)) or "ok")
    mod.STATE.edit_target = {"ids": [4595608454], "date": today.date(), "contiguous": True}
    return calls, today


def test_split_edit_points_keeps_code_trailing():
    mod = _load()
    text, pts = mod._split_edit_points("lx chat @xk88 0947-1002 [17]")
    assert (text, pts) == ("lx chat @xk88 0947-1002", 17)
    assert mod._parse_edit_text(text) == ("lx chat", "xk88", ("0947", "1002"), [])
    assert mod._split_edit_points("lx chat @xk88 0947-") == ("lx chat @xk88 0947-", None)


def test_closed_range_with_points_retimes_then_runs_done(monkeypatch):
    mod = _load()
    calls, today = _arm_running(mod, monkeypatch)
    mod.input_buffer.text = "lx chat @xk88 0947-1002 [17]"
    _enter(mod).handler(_Event())

    updates = [c for c in calls if c[0] == "update"]
    assert len(updates) == 1
    fields = updates[0][2]
    assert "[17]" not in fields.get("description", "") and "@xk88" not in fields.get("description", "")
    assert fields["stop"] == today.replace(hour=10, minute=2).isoformat()
    dids = [c for c in calls if c[0] == "did"]
    assert dids == [("did", "lx chat 0947-1002 @xk88 [17]")], calls


def test_open_ended_with_points_parks_value_inline_no_credit(monkeypatch):
    mod = _load()
    calls, _today = _arm_running(mod, monkeypatch)
    mod.input_buffer.text = "lx chat @xk88 0947- [17]"
    _enter(mod).handler(_Event())

    updates = [c for c in calls if c[0] == "update"]
    assert len(updates) == 1
    assert updates[0][2]["description"] == "lx chat [17]"
    assert updates[0][2]["project_id"] == mod.PROJECT_MAP["xk88"]
    assert not [c for c in calls if c[0] == "did"], "still running: no 分 yet"
    assert mod._resolvable_points("lx chat [17]") == 17
