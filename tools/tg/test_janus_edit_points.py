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


def _arm_logged(mod, monkeypatch, tmp_path, pts=17, ids=(4595608454,), desc="lx chat"):
    """A finished "lx chat" @xk88 row, 09:47-10:02 today, logged for `pts`."""
    calls, today = _arm_running(mod, monkeypatch)
    start = today.replace(hour=9, minute=47)
    pid = mod.PROJECT_MAP["xk88"]
    mod.STATE.current = None
    mod.STATE.entries = [{"id": i, "desc": desc, "start_dt": start, "project_id": pid,
                          "end_dt": today.replace(hour=10, minute=2), "tags": []} for i in ids]
    monkeypatch.setattr(mod, "_ENTRY_PTS_DIR", tmp_path)
    monkeypatch.setattr(mod, "toggl_project_code", lambda p, d="": "xk88")
    mod._entry_pts_add(today.date(), {ids[0]: pts})
    item = {"kind": "entry", "raw_desc": desc, "dur_min": 15, "start_dt": start,
            "entry_ids": list(ids), "project_id": pid, "contiguous": True}
    jobs = []
    monkeypatch.setattr(mod, "_enqueue_work", lambda app, label, job, key=None: jobs.append(job))
    return calls, today, item, jobs


def _submit(mod, item, today, text, jobs):
    mod.STATE.edit_target = {"ids": item["entry_ids"], "date": today.date(), "contiguous": True,
                             "shown_pts": mod._edit_shown_points(item), "row": item}
    mod.input_buffer.text = text
    _enter(mod).handler(_Event())
    import asyncio
    for job in jobs:
        asyncio.run(job())


def test_prefill_shows_a_logged_rows_points(monkeypatch, tmp_path):
    mod = _load()
    _, _, item, _ = _arm_logged(mod, monkeypatch, tmp_path)
    shown = mod._edit_shown_points(item)
    assert mod._prefill_with_points(mod._entry_edit_prefill(item), shown) == "lx chat @xk88 0947-1002 [17]"


def test_prefill_replaces_an_inline_bracket_in_the_desc(monkeypatch, tmp_path):
    mod = _load()
    assert mod._prefill_with_points("x [17] @i9 0900-0930", 42) == "x @i9 0900-0930 [42]"


def test_no_points_shown_for_running_unlogged_or_negative_rows(monkeypatch, tmp_path):
    mod = _load()
    _, today, item, _ = _arm_logged(mod, monkeypatch, tmp_path)
    assert mod._edit_shown_points({**item, "running": True}) is None
    assert mod._edit_shown_points({**item, "entry_ids": [999]}) is None
    mod._entry_pts_add(today.date(), {item["entry_ids"][0]: -5})
    assert mod._edit_shown_points(item) is None
    assert mod._prefill_with_points("x 0900-0930", None) == "x 0900-0930"


def test_resubmitting_shown_points_never_credits_again(monkeypatch, tmp_path):
    mod = _load()
    calls, today, item, jobs = _arm_logged(mod, monkeypatch, tmp_path)
    _submit(mod, item, today, "lx chat @xk88 0947-1002 [17]", jobs)
    assert not [c for c in calls if c[0] == "did"]
    assert mod._recorded_entry_pts(today.date(), item["entry_ids"]) == 17


def test_shown_zero_resubmit_never_credits(monkeypatch, tmp_path):
    mod = _load()
    calls, today, item, jobs = _arm_logged(mod, monkeypatch, tmp_path, pts=0)
    _submit(mod, item, today, "lx chat @xk88 0947-1002 [0]", jobs)
    assert not [c for c in calls if c[0] == "did"]


def test_changing_shown_points_logs_only_the_difference(monkeypatch, tmp_path):
    mod = _load()
    calls, today, item, jobs = _arm_logged(mod, monkeypatch, tmp_path)
    _submit(mod, item, today, "lx chat @xk88 0947-1002 [25]", jobs)
    did = [c[1] for c in calls if c[0] == "did"]
    assert did == [mod._points_adjust_cmd("lx chat", "xk88", 8, item["start_dt"], item["start_dt"] + mod.dt.timedelta(minutes=15), False)]
    assert mod._recorded_entry_pts(today.date(), item["entry_ids"]) == 25


def test_failed_edit_posts_no_points_change(monkeypatch, tmp_path):
    mod = _load()
    calls, today, item, jobs = _arm_logged(mod, monkeypatch, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("stale id")
    monkeypatch.setattr(mod.toggl_api, "update_entry", boom, raising=False)
    _submit(mod, item, today, "lx chat @xk88 0947-1002 [25]", jobs)
    assert jobs == [] and not [c for c in calls if c[0] == "did"]
    assert mod._recorded_entry_pts(today.date(), item["entry_ids"]) == 17


def test_deleting_the_bracket_changes_nothing(monkeypatch, tmp_path):
    mod = _load()
    calls, today, item, jobs = _arm_logged(mod, monkeypatch, tmp_path)
    _submit(mod, item, today, "lx chat @xk88 0947-1002", jobs)
    assert not [c for c in calls if c[0] == "did"]
    assert mod._recorded_entry_pts(today.date(), item["entry_ids"]) == 17


def test_merged_row_keeps_its_total_after_a_retime(monkeypatch, tmp_path):
    mod = _load()
    calls, today, item, jobs = _arm_logged(mod, monkeypatch, tmp_path, pts=5, ids=(1, 2))
    mod._entry_pts_add(today.date(), {2: 7})
    monkeypatch.setattr(mod.toggl_api, "delete_entry", lambda i: calls.append(("delete", i)), raising=False)
    _submit(mod, item, today, "lx chat @xk88 0947-1002 [12]", jobs)
    assert ("delete", 2) in calls
    assert mod._recorded_entry_pts(today.date(), [1]) == 12


def test_reopening_a_logged_row_changes_no_points(monkeypatch, tmp_path):
    mod = _load()
    calls, today, item, jobs = _arm_logged(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_open_ended_fields", lambda s, r: {"start": s.isoformat()})
    _submit(mod, item, today, "lx chat @xk88 0947- [25]", jobs)
    assert jobs == [] and not [c for c in calls if c[0] == "did"]


def test_rate_sheet_row_correction_stores_the_new_total(monkeypatch, tmp_path):
    """A 69m per-minute row: ledger 0, shown [69]. Correcting to 80 must leave
    80 on record, not the 11 a sum into the 0 would."""
    mod = _load()
    calls, today, item, jobs = _arm_logged(mod, monkeypatch, tmp_path, pts=0)
    monkeypatch.setattr(mod, "_row_points", lambda *a: (True, 69))
    _submit(mod, item, today, "lx chat @xk88 0947-1002 [80]", jobs)
    assert [c for c in calls if c[0] == "did"]
    assert mod._recorded_entry_pts(today.date(), item["entry_ids"]) == 80


# ── lib/entry_points.py: a row's 分 are the same on every client and host ─────

def _shared(mod, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    monkeypatch.setenv("ENTRY_POINTS_DIR", str(shared))
    monkeypatch.setattr(mod, "_ENTRY_PTS_DIR", tmp_path)


def test_a_record_from_another_host_shows_on_the_row(monkeypatch, tmp_path):
    mod = _load()
    _shared(mod, tmp_path, monkeypatch)
    day = dtm.date(2026, 11, 5)
    mod.entry_points.record(day, {"4612720841": 19})
    assert mod._recorded_entry_pts(day, [4612720841]) == 19


def test_pin_tombstones_the_merged_ids_so_they_cant_come_back(monkeypatch, tmp_path):
    mod = _load()
    _shared(mod, tmp_path, monkeypatch)
    day = dtm.date(2026, 11, 5)
    mod._entry_pts_add(day, {1: 10, 2: 20})
    mod._entry_pts_pin(day, [1, 2], 30)
    assert mod._recorded_entry_pts(day, [1, 2]) == 30


def test_a_delta_sums_on_the_shared_total_not_the_local_blank(monkeypatch, tmp_path):
    mod = _load()
    _shared(mod, tmp_path, monkeypatch)
    day = dtm.date(2026, 11, 5)
    mod.entry_points.record(day, {"7": 19})      # logged on the other machine
    mod._entry_pts_add(day, {7: 5}, add=True)
    assert mod._recorded_entry_pts(day, [7]) == 24
    assert mod.entry_points.for_day(day)["7"] == 24


def test_done_refuses_a_row_logged_anywhere_else():
    src = (HERE / "janus.py").read_text()
    body = src[src.index("if _cmd_done_today(cmd):"):]
    guard = body.index('already logged [{rec}]')
    assert guard < body.index("STATE.event_sel = None")
