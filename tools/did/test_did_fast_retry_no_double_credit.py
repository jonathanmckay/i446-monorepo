#!/usr/bin/env python3
"""Regression test (bug 2026-08-31): dtd's auto-retry loop re-invokes
did-fast.py as a brand-new process for a completion it believes failed to
land. Each retry starts with a fresh, empty `claimed_task_ids` set, so
nothing stopped it from re-matching (via `--task-id`) and re-crediting a
Todoist task an EARLIER, already-successful attempt had already closed and
recorded.

Real-world case: "1 groceries 8.20" (a dated one-off copy of the "groceries"
1n+ habit — its dotted "M.D" suffix doesn't match parse_input's "M/D"
trailing-date-strip regex, so it never resolves through the 1n+ alias table
and always falls to Step 0.3's generic Todoist-match path) got its +20 fen
credit appended to 0分!W NINE times for a single real Todoist completion,
producing `=hcbi!AA245+hcbi!Y245+20+20+20+20+20+20+20+20+20` before dtd's
retry cap (default 8) finally gave up. Todoist's own activity log showed
exactly one completion event for the task.

Fix: `route_items` now seeds `claimed_task_ids` with the ids already
recorded in today's completed-today.json (`mc.COMPLETED`'s `ids` map, written
at Step 7 of every successful did-fast.py run) — the one piece of state that
survives across separate process invocations — so a retried completion for
an already-recorded task_id lands in the existing "already matched+credited"
skip branch instead of being credited again.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
DID_FAST = HERE / "did-fast.py"

_SPEC = importlib.util.spec_from_file_location("did_fast_retry_test", DID_FAST)
_df = importlib.util.module_from_spec(_SPEC)
sys.modules["did_fast_retry_test"] = _df  # so dataclasses resolve their __module__
_SPEC.loader.exec_module(_df)  # type: ignore[union-attr]


class RetryDoesNotDoubleCreditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.completed_path = Path(self.tmp.name) / "completed-today.json"
        self._saved_completed = _df.mc.COMPLETED
        _df.mc.COMPLETED = self.completed_path
        self.addCleanup(setattr, _df.mc, "COMPLETED", self._saved_completed)

        # Empty 0n/1n+ registries force routing through Step 0.3 (generic
        # Todoist match) — the path this specific item takes for real,
        # since its dotted "8.20" suffix never strips into target_date.
        self.headers = {"0n": {}, "1n": {}}
        self.task = {
            "id": "999",
            "content": "1 groceries 8.20 (15) [20]",
            "labels": ["1neon", "hcb"],
        }
        self.tq = {"0neon": [], "1neon": [self.task], "夜neon": []}
        self.item = _df.ParsedItem(
            raw="1 groceries 8.20", name="1 groceries 8.20", points_override=20)

    def _route(self):
        return _df.route_items([self.item], self.headers, self.tq,
                                preferred_id="999")

    def test_first_attempt_credits_fen_points(self):
        r = self._route()[0]
        self.assertEqual(r.step, "todoist")
        self.assertEqual(r.fen_col, "W")
        self.assertEqual(r.fen_points, 20)

    def test_retry_after_recorded_completion_does_not_recredit(self):
        # First (successful) attempt.
        r1 = self._route()[0]
        self.assertEqual(r1.fen_points, 20)

        # Step 7 of a real did-fast.py run records the closed task's id
        # against today's date once the pipeline finishes successfully.
        _df.mc.append_names(["1 groceries 8.20"], ids={"1 groceries 8.20": "999"})

        # dtd's auto-retry loop re-invokes did-fast as a fresh process for
        # the SAME completion (same --task-id), believing the first attempt
        # failed to land.
        r2 = self._route()[0]
        self.assertEqual(r2.step, "skipped",
                          "a task_id already recorded in completed-today.json "
                          "must not be re-matched and re-credited")
        self.assertIsNone(r2.fen_col)
        self.assertEqual(r2.fen_points, 0)

    def test_nine_retries_credit_points_exactly_once(self):
        """Mirrors the real incident: simulate the full 1 + 8-retry sequence
        dtd's backoff produces and confirm only the first credits points."""
        total_credited = 0
        for attempt in range(9):
            r = self._route()[0]
            if r.fen_col:
                total_credited += r.fen_points
                _df.mc.append_names(["1 groceries 8.20"],
                                    ids={"1 groceries 8.20": "999"})
        self.assertEqual(total_credited, 20,
                          "9 retried invocations of the same completion must "
                          "credit +20 exactly once, not once per attempt")

    def test_different_day_completion_does_not_suppress_new_credit(self):
        # A stale (yesterday's) completed-today record must not block a
        # genuinely new completion of the same task id today.
        self.completed_path.write_text(json.dumps(
            {"date": "1999-01-01", "names": [], "ids": {"1 groceries 8.20": "999"}}))
        r = self._route()[0]
        self.assertEqual(r.fen_col, "W")
        self.assertEqual(r.fen_points, 20)


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call(["python3", "-m", "pytest", __file__, "-v"]))
