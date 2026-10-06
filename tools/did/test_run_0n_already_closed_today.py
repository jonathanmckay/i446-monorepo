"""Regression test: run.py's run_0n re-closed a daily 0neon card that was
already closed for the day.

Bug 2026-10-06 ("I don't see 0g on the list of tasks for today"): run_0n
closed the matched card with no due-date guard (run_1n got one on 2026-09-21,
did-fast has its own). A second /did 0g found the card already rolled to
tomorrow and closed it again, so "every day" advanced to the day after; dtd
reads recurring due > today as done, and 0g vanished while unmarked.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

_RUN_SPEC = importlib.util.spec_from_file_location("did_run_0n_closed", _HERE / "run.py")
run = importlib.util.module_from_spec(_RUN_SPEC)
sys.modules["did_run_0n_closed"] = run
_RUN_SPEC.loader.exec_module(run)  # type: ignore[union-attr]


class _FakeExcel:
    def __init__(self):
        self.writes = []

    def read(self, sheet, col, **kw):
        return {"ok": True, "value": "0"}

    def write(self, sheet, col, **kw):
        self.writes.append((sheet, col, kw))
        return {"ok": True}

    def append(self, sheet, col, **kw):
        return {"ok": True}


class _FakeTodoist:
    """One 'every day' card; close_task rolls it forward a day."""

    def __init__(self, content: str, due: date):
        self.card = {"id": "6gHVV8RP74W6rHf6", "content": content,
                     "labels": ["0neon", "g245"],
                     "due": {"date": due.isoformat(), "is_recurring": True}}
        self.closes = 0

    def find_tasks(self, labels=None, limit=50, **kw):
        return [self.card]

    def close_task(self, tid):
        self.closes += 1
        d = date.fromisoformat(self.card["due"]["date"]) + timedelta(days=1)
        self.card["due"]["date"] = d.isoformat()


def _run(td, ex, name, target):
    d = {"habit_name": name, "name": name, "neon_sheet": "0n", "neon_col": "T", "domain": "g245",
         "todoist_label": "0neon", "aliases": [], "toggl": {}}
    with patch.object(run, "excel", ex), patch.object(run, "todoist", td), \
         patch.object(run, "_target_date_obj",
                      side_effect=lambda s: date(2026, *map(int, s.split("/")))), \
         patch.object(run, "_today_md", return_value=target), \
         patch.object(run, "_auto_detect_minutes", return_value=1), \
         patch.object(run, "_drop_from_queue"), \
         patch.object(run, "_append_completed"), \
         patch.object(run, "_fire_refresh", create=True):
        return run.run_0n(d, name, target, None)


def test_repeat_did_same_day_closes_once():
    td, ex = _FakeTodoist("0g (4) [8]", due=date(2026, 10, 5)), _FakeExcel()
    for _ in range(2):  # two /did 0g on 10/5
        assert _run(td, ex, "0g", "10/5") == 0
    assert td.closes == 1
    assert td.card["due"]["date"] == "2026-10-06", "must not drift to 10/07"


def test_advance_allowed_habit_may_close_one_day_early_only():
    td, ex = _FakeTodoist("push (10) [20]", due=date(2026, 10, 6)), _FakeExcel()
    assert _run(td, ex, "push", "10/5") == 0       # due tomorrow: allowed
    assert td.closes == 1
    assert _run(td, ex, "push", "10/5") == 0       # due day-after: refused
    assert td.closes == 1 and td.card["due"]["date"] == "2026-10-07"
