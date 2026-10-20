"""Bug 2026-10-19: "dtd is still showing teams while Janus CLI has it right."

dtd's running row came from a PER-INSTANCE /tmp timer file, so only the pane
that started a task knew about it. running_timer is the shared reading of
Toggl both ends use instead.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import running_timer as rt  # noqa: E402

QUEUE = {"today": [{"id": "T1", "content": "teams (4) [10]"}],
         "1neon": [{"id": "W1", "content": "1 f693 (30) [30]"}]}


def _fixture(tmp_path, entry, age=5.0):
    q = tmp_path / "task-queue.json"
    q.write_text(json.dumps(QUEUE))
    c = tmp_path / "toggl-current.json"
    c.write_text(json.dumps({"ts": time.time() - age, "entry": entry}))
    return c, q


def _run(tmp_path, entry, age=5.0, **kw):
    c, q = _fixture(tmp_path, entry, age)
    return rt.observed(cache=c, queue_path=q, **kw)


def test_running_entry_resolves_its_dtd_task(tmp_path):
    o = _run(tmp_path, {"description": "1 f693", "start": "2026-10-19T20:31:00+00:00"})
    assert o["desc"] == "1 f693" and o["task_id"] == "W1"
    assert o["started"] == 1792441860 and o["ts"] > 0


def test_idle_reading_is_reported_not_swallowed(tmp_path):
    # The whole point: "nothing is running" must reach the caller, or a stale
    # row can never be cleared.
    o = _run(tmp_path, None)
    assert o is not None and o["desc"] == "" and o["started"] == 0 and o["task_id"] == ""


def test_a_timer_naming_no_task_still_reports_the_description(tmp_path):
    o = _run(tmp_path, {"description": "B2", "start": "2026-10-19T19:19:00+00:00"})
    assert o["desc"] == "B2" and o["task_id"] == ""


def test_stale_or_missing_reading_is_ignored(tmp_path):
    assert _run(tmp_path, {"description": "teams"}, age=rt.MAX_AGE_S + 60) is None
    assert rt.observed(cache=tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert rt.observed(cache=bad) is None


def test_zulu_start_parses_on_python39(tmp_path):
    o = _run(tmp_path, {"description": "teams", "start": "2026-10-19T20:31:00Z"})
    assert o["started"] == 1792441860 and o["task_id"] == "T1"


def test_norm_folds_annotations_dashes_and_case(tmp_path):
    assert rt.norm("1 F693  (30) [30]") == rt.norm("1 f693")
    assert rt.norm("check-in with Bei") == "check in with bei"
    assert rt.norm("   ") == ""
