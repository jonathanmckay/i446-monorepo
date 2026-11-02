"""Regression (2026-11-02): desktop janus double-credited a per-block split.

After 42d6f30a, did-fast splits a >100分 per-minute entry into one Toggl
entry per 地支 block. Desktop ⌥↵ guarded only by the exact command string
("xk20 1100-1400 @xk87"), so ⌥↵ on a resulting segment row ("xk20 1300-1400
@xk87") re-ran did-fast and credited those minutes a second time. Mobile
guards by Toggl id (mobile.py _ledger_segments); desktop now does the same."""
import asyncio
import datetime as dt
import inspect
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import janus  # noqa: E402

SPLIT_STDOUT = "did-fast\n" + json.dumps({"results": [
    {"name": "xk20", "step": "0n", "block": "午", "toggl": {"ids": [111]}},
    {"name": "xk20", "step": "0n", "block": "未", "split": True, "toggl": {"ids": [222]}},
]})


def _state(tmp_path, monkeypatch):
    f = tmp_path / "janus-done-cmds.json"
    monkeypatch.setattr(janus, "_DONE_CMDS_TODAY", f)
    return f


def test_did_toggl_ids_collects_every_segment():
    assert janus._did_toggl_ids(SPLIT_STDOUT) == ["111", "222"]
    assert janus._did_toggl_ids("no json here") == []


def test_entry_ledger_roundtrip_and_cmds_preserved(tmp_path, monkeypatch):
    f = _state(tmp_path, monkeypatch)
    janus._mark_cmd_done_today("xk20 1100-1400 @xk87")
    janus._mark_entries_done_today(["111", 222])
    assert janus._entries_done_today([222])
    assert janus._entries_done_today(["111", "999"])
    assert not janus._entries_done_today(["999"])
    assert not janus._entries_done_today([])
    assert janus._cmd_done_today("xk20 1100-1400 @xk87")
    janus._mark_cmd_done_today("other 0900-1000")
    assert janus._entries_done_today(["111"]), "cmd write must keep the id ledger"
    data = json.loads(f.read_text())
    assert data["ids"] == ["111", "222"]


def test_entry_ledger_resets_on_new_day(tmp_path, monkeypatch):
    f = _state(tmp_path, monkeypatch)
    f.write_text(json.dumps({"date": "2000-01-01", "cmds": [], "ids": ["111"]}))
    assert not janus._entries_done_today(["111"])


def test_run_did_fast_ids_and_str_wrapper(monkeypatch):
    monkeypatch.setattr(janus, "_did_flags", lambda: ())
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout=SPLIT_STDOUT, stderr=""))
    summary, ids = janus.run_did_fast_ids("xk20 1100-1400 @xk87")
    assert ids == ["111", "222"]
    assert isinstance(janus.run_did_fast("xk20 1100-1400 @xk87"), str)


def test_row_did_ledgers_split_segments_so_segment_press_is_refused(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    captured = {}
    monkeypatch.setattr(janus, "_enqueue_work",
                        lambda app, label, fn, key=None: captured.setdefault("fn", fn))
    monkeypatch.setattr(janus, "flash", lambda *a, **k: None)
    monkeypatch.setattr(janus, "fetch_today", lambda *a, **k: None)
    monkeypatch.setattr(janus, "fetch_points", lambda *a, **k: None)
    monkeypatch.setattr(janus, "run_did_fast_ids", lambda cmd, flags=None: ("ok", ["111", "222"]))

    class App:
        def invalidate(self):
            pass

    janus._run_row_did(App(), "xk20 1100-1400 @xk87", "xk20 1100-1400 @xk87",
                       entry_ids=["orig-1"])
    asyncio.run(captured["fn"]())
    # The segment row's command differs from the pressed one ...
    assert not janus._cmd_done_today("xk20 1300-1400 @xk87")
    # ... but its Toggl entry is ledgered, which the ⌥↵ handler checks.
    assert janus._entries_done_today(["222"])
    assert janus._entries_done_today(["orig-1"])


def test_row_handler_checks_entry_ledger_before_running():
    src = inspect.getsource(janus)
    assert 'if _cmd_done_today(cmd) or _entries_done_today(item.get("entry_ids")):' in src
    assert '_run_row_did(event.app, cmd, cmd, entry_ids=item.get("entry_ids"))' in src
    assert 'entry_ids=dtgt.get("entry_ids")' in src
    done_src = inspect.getsource(janus._run_done_command)
    assert "_mark_entries_done_today(ids)" in done_src
