"""Bug 2026-10-20: "janus won't let me use arrows to navigate entries".

Two ways the cursor died, both driven through the REAL key processor:
1. An armed edit (select an entry, ⏎) pre-fills the command line, and ↑/↓
   were gated on an empty line, so nothing moved until Esc. Arrows now cancel
   the armed edit and move; a hand-typed command still keeps the gate.
2. The running-timer row was keyed on its start_dt, which the optimistic and
   fetched views disagree on by seconds, so the selection never matched and
   every press was a fresh "most recent row" selection: stuck on the running
   row. The key is now the entry id (start floored to the minute before the
   id is known), and a stale ("current", …) selection still counts as it.
"""
import asyncio
import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_arrows", HERE / "janus.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


J = _load()


NOW = J.view_now().replace(microsecond=0)


def _item(i, kind="entry", secs=0):
    return {"kind": kind, "start_dt": NOW - dt.timedelta(hours=3 - i, seconds=secs),
            "dur_min": 30, "entry_ids": [100 + i], "raw_desc": f"e{i}", "project_id": None}


def _press(key):
    from prompt_toolkit.key_binding.key_processor import KeyPress
    from prompt_toolkit.application.current import set_app
    with set_app(J.app):
        J.app.key_processor.feed(KeyPress(key))
        J.app.key_processor.process_keys()


def _run(coro):
    async def wrap():
        J.app.loop = asyncio.get_running_loop()
        return await coro()
    return asyncio.run(wrap())


def _hhmm(sel):
    return sel[1].strftime("%H:%M") if sel and isinstance(sel[1], dt.datetime) else sel


def test_arrows_move_while_an_edit_is_armed_and_cancel_it():
    from prompt_toolkit.keys import Keys

    async def body():
        J.STATE.visible_events = [_item(0), _item(1), _item(2)]
        J.STATE.event_sel = J._sel_key(_item(2))
        J.STATE.edit_target = {"id": 102}
        J.input_buffer.text = "e2 @hcb 1200-1230"
        _press(Keys.Up)
        assert J.STATE.event_sel == J._sel_key(_item(1)), "↑ must move even with the edit prefill"
        assert J.STATE.edit_target is None and J.input_buffer.text == "", "the armed edit is cancelled"
        _press(Keys.Down)
        assert J.STATE.event_sel == J._sel_key(_item(2))
    _run(body)


def test_typed_command_still_gates_arrows():
    from prompt_toolkit.keys import Keys

    async def body():
        J.STATE.visible_events = [_item(0), _item(1), _item(2)]
        J.STATE.event_sel = None
        J.STATE.edit_target = None
        J.STATE.split_target = None
        J.input_buffer.text = "coffee @hcb"
        _press(Keys.Down)
        assert J.STATE.event_sel is None
        assert J.input_buffer.text == "coffee @hcb", "typed text is never thrown away"
    _run(body)


def test_running_row_key_is_stable_across_start_drift():
    a = J._sel_key({"kind": "current", "start_dt": J.view_now(), "entry_ids": [7]})
    b = J._sel_key({"kind": "current", "start_dt": J.view_now() - dt.timedelta(seconds=9), "entry_ids": [7]})
    assert a == b == ("current", (7,))
    # a switch to another entry is still a different key
    assert J._sel_key({"kind": "current", "start_dt": J.view_now(), "entry_ids": [8]}) != a
    # before the id is known: minute granularity
    t = J.view_now().replace(second=30, microsecond=0)
    assert J._sel_key({"kind": "current", "start_dt": t, "entry_ids": []}) == \
        J._sel_key({"kind": "current", "start_dt": t + dt.timedelta(seconds=20), "entry_ids": []})


def test_cursor_leaves_the_running_row_even_if_its_key_moved():
    from prompt_toolkit.keys import Keys

    async def body():
        J.input_buffer.text = ""
        J.STATE.edit_target = None
        J.STATE.event_sel = ("current", NOW, (102,))   # a stale pre-fix key shape
        J.STATE.visible_events = [_item(0), _item(1), _item(2, "current")]
        _press(Keys.Up)
        assert J.STATE.event_sel == J._sel_key(_item(1)), "↑ from a stale running-row selection goes to the previous entry"
    _run(body)
