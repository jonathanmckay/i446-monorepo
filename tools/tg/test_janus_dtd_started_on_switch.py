"""Bug 2026-10-19: "abacus emoji is not on tasks that I start, it should be."

dtd draws 🧮 from ~/.local/state/jm/dtd-started and the live spinner from the
shared dtd-running-hint. Only dtd's own start.sh (Enter/ctrl-s) and janus's
/d path ever wrote them, so an ORDINARY janus switch -- typing a task name in
the janus CLI, which is how most work actually starts -- told dtd nothing:
no spinner, no abacus, and dtd's own stale timer file kept pointing at
whatever was last started inside dtd.
"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "janus.py").read_text()

QUEUE = {
    "updated": "2026-10-19T19:00:00-07:00",
    "today": [{"id": "T1", "content": "5^3 qz12 report (30) [30]", "labels": ["qz12"]}],
    "1neon": [{"id": "W1", "content": "1 f693 (30) [30]", "labels": ["i9"]}],
}


def _load():
    spec = importlib.util.spec_from_file_location("janus_started_switch", HERE / "janus.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


J = _load()


def _setup(tmp_path, monkeypatch):
    q = tmp_path / "task-queue.json"
    q.write_text(json.dumps(QUEUE))
    monkeypatch.setattr(J, "TASK_QUEUE", q)
    monkeypatch.setattr(J, "DTD_RUNNING_HINT", tmp_path / "dtd-running-hint")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "state" / "jm" / "dtd-started"


def test_switch_to_a_known_task_records_started_and_the_running_hint(tmp_path, monkeypatch):
    started = _setup(tmp_path, monkeypatch)
    J.mark_dtd_started("1 f693")
    day, tid, ts = started.read_text().strip().split("\t")
    assert tid == "W1" and ts.isdigit() and day.count("-") == 2
    name, hts, hid = J.DTD_RUNNING_HINT.read_text().split("\t")
    assert (name, hid) == ("1 f693", "W1") and hts.isdigit()


def test_switch_to_an_unknown_description_clears_the_stale_hint(tmp_path, monkeypatch):
    started = _setup(tmp_path, monkeypatch)
    J.write_dtd_running_hint({"id": "W1", "content": "1 f693 (30) [30]"})
    J.mark_dtd_started("lunch with nobody")
    assert not J.DTD_RUNNING_HINT.exists()
    assert not started.exists(), "an unknown timer name must not fake a started task"


def test_lookup_ignores_annotations_and_case(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert J.dtd_task_for_desc("5^3 QZ12 report")["id"] == "T1"
    assert J.dtd_task_for_desc("") is None
    assert J.dtd_task_for_desc("no such task") is None


def test_the_switch_path_marks_the_task_started():
    i_mark = SRC.index("mark_dtd_started(desc)")
    i_opt = SRC.index("_optimistic_start(desc, raw=bare)")
    assert i_mark < i_opt, "dtd must learn about the start before the row repaints"
    assert "clear_dtd_running_hint()  # the timer moves off any /d task" not in SRC
