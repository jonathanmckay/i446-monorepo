"""Regression test: run.py's run_1n re-closed a weekly 1neon card that was
already done for the week.

Bug 2026-09-21 (seen 2026-10-05, "1-2g is not on my list although it should
be"): three /1-2g runs that Monday each ended in `/did 1 -2g`. run_1n had no
due-date guard, so every run appended +20 to 0分 and closed the "every Monday"
card again. It rolled 9/28 → 10/5 → 10/12 and was missing from the 10/5 list.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

_RUN_SPEC = importlib.util.spec_from_file_location("did_run_1n_week", _HERE / "run.py")
run = importlib.util.module_from_spec(_RUN_SPEC)
sys.modules["did_run_1n_week"] = run
_RUN_SPEC.loader.exec_module(run)  # type: ignore[union-attr]


class _FakeExcel:
    def __init__(self):
        self.writes, self.appends = [], []

    def read(self, sheet, col, **kw):
        return {"ok": True, "value": "20"}  # row 5: fixed points

    def write(self, sheet, col, **kw):
        self.writes.append((sheet, col, kw))
        return {"ok": True}

    def append(self, sheet, col, **kw):
        self.appends.append((sheet, col, kw))
        return {"ok": True}


class _FakeTodoist:
    """One 'every Monday' card; close_task rolls it forward a week."""

    def __init__(self, due: date):
        self.card = {"id": "6gJPwc8cMvrWQqhc", "content": "1 -2g (13) [20]",
                     "labels": ["1neon", "g245"],
                     "due": {"date": due.isoformat(), "is_recurring": True}}
        self.closes = 0

    def find_tasks(self, labels=None, limit=50, **kw):
        return [self.card]

    def close_task(self, tid):
        self.closes += 1
        d = date.fromisoformat(self.card["due"]["date"]) + timedelta(days=7)
        self.card["due"]["date"] = d.isoformat()


_D = {"habit_name": "1 -2g", "neon_col": "K", "fen_col": "Q",
      "todoist_label": "1neon", "aliases": [], "toggl": {}}


def _run(td: _FakeTodoist, ex: _FakeExcel, target: str) -> int:
    with patch.object(run, "excel", ex), patch.object(run, "todoist", td), \
         patch.object(run, "_calc_mw", return_value=(9.3, 42)), \
         patch.object(run, "_target_date_obj",
                      side_effect=lambda s: date(2026, *map(int, s.split("/")))), \
         patch.object(run, "_auto_detect_minutes", return_value=0), \
         patch.object(run, "_drop_from_queue"), \
         patch.object(run, "_append_completed"), \
         patch.object(run, "_fire_refresh"):
        return run.run_1n(dict(_D), target)


def test_repeat_did_same_week_closes_and_credits_once():
    td, ex = _FakeTodoist(due=date(2026, 9, 21)), _FakeExcel()
    for _ in range(3):  # the three /1-2g runs on Mon 9/21
        assert _run(td, ex, "9/21") == 0
    assert td.closes == 1, "card must not roll past next week"
    assert td.card["due"]["date"] == "2026-09-28"
    assert len(ex.appends) == 1, "0分 must be credited +20 once, not per run"
    assert len(ex.writes) == 1


def test_early_completion_within_week_still_closes():
    # Thursday card completed on Monday of the same Sun-Sat week.
    td, ex = _FakeTodoist(due=date(2026, 9, 24)), _FakeExcel()
    assert _run(td, ex, "9/21") == 0
    assert td.closes == 1
    assert len(ex.appends) == 1


def test_overdue_card_still_closes():
    td, ex = _FakeTodoist(due=date(2026, 9, 14)), _FakeExcel()
    assert _run(td, ex, "9/21") == 0
    assert td.closes == 1
    assert len(ex.appends) == 1
