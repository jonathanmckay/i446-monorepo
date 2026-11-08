"""done_session: which tasks /done closes, and when a run counts as closed."""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"{name}_t", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ds = _mod("done_session")
outcome = _mod("dtd_outcome")


def test_done_claims_count_released_do_not():
    # agent_claims.py done empties held(), so the ✅ claim must still be found;
    # a released claim is a task this session handed back, never closed by /done.
    claims = {"1": {"session": "s", "state": "done"},
              "2": {"session": "s", "state": "released"},
              "3": {"session": "other", "state": "done"},
              "4": {"session": "s", "state": "working"}}
    assert ds.session_tasks("s", claims, ["4"]) == ["4", "1"]
    assert ds.session_tasks("s", claims, ["1"]) == ["1"]


def test_content_prefers_live_cache_over_claim_record():
    claims = {"9": {"task": "old name"}}
    assert ds.current_content("9", claims, [{"id": "9", "content": "new name [10]"}]) == "new name [10]"
    assert ds.current_content("9", claims, []) == "old name"


def _run(todoist):
    return json.dumps({"results": [{"name": "fix x", "step": "todoist", "todoist": todoist}]})


def test_closed_requires_todoist_close(monkeypatch):
    monkeypatch.setattr(outcome, "_completed_today", lambda: (set(), set()))
    assert ds.closed_ok(0, _run({"id": "7", "closed": True}), "7", "fix x", outcome)[0]
    ok, display = ds.closed_ok(0, _run({"id": "7", "closed": False}), "7", "fix x", outcome)
    assert not ok and "Todoist close failed" in display


def test_failed_run_is_not_closed():
    assert not ds.closed_ok(1, "", "7", "fix x", outcome)[0]
    assert not ds.closed_ok(0, "  ", "7", "fix x", outcome)[0]


def test_timer_line():
    raw = json.dumps({"toggl_stopped": {"description": "fix x", "stopped": True, "minutes": 12}})
    assert ds.timer_line(raw) == "stopped fix x (12m)"
    assert ds.timer_line(json.dumps({"toggl_stopped": None})) is None
