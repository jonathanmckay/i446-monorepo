"""Regression test for run.py's run_1n() ignoring a typed HHMM-HHMM time range.

Bug 2026-08-11: `/did 一起饭 0830-0846` (a cumulative 1n+ habit) wrote "1m" to
the week cell and created no Toggl entry, even though the user typed an
explicit 16-minute range. run_0n already prioritized explicit > time_range >
Toggl auto-detect > 1 and created a matching Toggl entry; run_1n did neither
— main() never even passed time_range into it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

_RUN_SPEC = importlib.util.spec_from_file_location("did_run_1n_tr", _HERE / "run.py")
run = importlib.util.module_from_spec(_RUN_SPEC)
sys.modules["did_run_1n_tr"] = run
_RUN_SPEC.loader.exec_module(run)  # type: ignore[union-attr]


class _FakeExcel:
    def __init__(self, read_value="0"):
        self.read_value = read_value
        self.writes = []
        self.appends = []

    def read(self, sheet, col, **kw):
        return {"ok": True, "value": self.read_value}

    def write(self, sheet, col, **kw):
        self.writes.append((sheet, col, kw))
        return {"ok": True, "value": kw.get("value")}

    def append(self, sheet, col, **kw):
        self.appends.append((sheet, col, kw))
        return {"ok": True, "value": kw.get("value")}


def _route_for(habit_query: str, target_date: str = "8/11") -> dict:
    import json
    import subprocess
    r = subprocess.run(
        ["python3", str(Path.home() / "i446-monorepo/tools/did/route.py"),
         habit_query, "--target-date", target_date],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


def test_run_1n_time_range_drives_minutes_not_default_one():
    """一起饭 is cumulative_increment=30: the week cell accumulates real
    minutes (old + this session's length), not old + 1."""
    d = _route_for("一起饭")
    assert d["step"] == "1n+" and d["cumulative_increment"] == 30
    fake = _FakeExcel(read_value="10")
    toggl_calls = []
    with patch.object(run, "excel", fake), \
         patch.object(run, "_calc_mw", return_value=(8.2, 37)), \
         patch.object(run, "_find_and_close_todoist", return_value=(None, None)), \
         patch.object(run, "_append_completed"), \
         patch.object(run, "_fire_refresh"), \
         patch.object(run.subprocess, "run",
                       side_effect=lambda *a, **k: toggl_calls.append(a[0]) or type("R", (), {"returncode": 0})()):
        rc = run.run_1n(d, "8/11", time_range=("0830", "0846"), explicit_minutes=None)
    assert rc == 0
    assert len(fake.writes) == 1
    sheet, col, kw = fake.writes[0]
    assert sheet == "1n+" and col == "AC"
    assert kw["value"] == "26", "old(10) + 16m from the typed range, not old(10) + default 1"


def test_run_1n_time_range_creates_toggl_entry():
    d = _route_for("一起饭")
    fake = _FakeExcel(read_value="0")
    toggl_calls = []

    def _fake_run(cmd, **kw):
        toggl_calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch.object(run, "excel", fake), \
         patch.object(run, "_calc_mw", return_value=(8.2, 37)), \
         patch.object(run, "_find_and_close_todoist", return_value=(None, None)), \
         patch.object(run, "_append_completed"), \
         patch.object(run, "_fire_refresh"), \
         patch.object(run.subprocess, "run", side_effect=_fake_run):
        run.run_1n(d, "8/11", time_range=("0830", "0846"), explicit_minutes=None)
    assert toggl_calls, "run_1n must create a Toggl entry when a time range was typed, same as run_0n"
    cmd = toggl_calls[0]
    assert "create" in cmd and "0830" in cmd and "0846" in cmd


def test_run_1n_explicit_minutes_still_wins_over_time_range():
    d = _route_for("一起饭")
    fake = _FakeExcel(read_value="0")
    with patch.object(run, "excel", fake), \
         patch.object(run, "_calc_mw", return_value=(8.2, 37)), \
         patch.object(run, "_find_and_close_todoist", return_value=(None, None)), \
         patch.object(run, "_append_completed"), \
         patch.object(run, "_fire_refresh"), \
         patch.object(run.subprocess, "run"):
        run.run_1n(d, "8/11", time_range=("0830", "0846"), explicit_minutes=99)
    sheet, col, kw = fake.writes[0]
    assert kw["value"] == "99", "explicit minutes must still outrank a typed time range"


def test_main_passes_time_range_into_run_1n():
    """Guard against re-regressing the call site: main() must thread
    time_range through to run_1n, not just explicit_minutes."""
    import ast
    src = (_HERE / "run.py").read_text()
    main_fn = [n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "main"][0]
    calls = [n for n in ast.walk(main_fn) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "run_1n"]
    assert calls, "expected a run_1n(...) call in main()"
    arg_names = [getattr(a, "id", None) for a in calls[0].args]
    assert "time_range" in arg_names, "main() must pass time_range to run_1n"
