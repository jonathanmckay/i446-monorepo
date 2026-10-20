#!/usr/bin/env python3
"""Regression (bug 2026-10-16: "0g didn't show up in dtd today"): Todoist's
POST /tasks/{id}/close is not idempotent for a recurring task -- each call
advances the recurrence. On 2026-10-15 the 0g close timed out client-side
after the server had processed it, close_todoist_task retried blindly, and
the daily 0g habit was closed twice 21s apart (07:33:38, 07:33:59), so its
due date jumped to 10/17 and the card never appeared on 10/16.

close_todoist_task must read the task back before retrying a transient
failure and skip the retry when the first close evidently landed (checked,
or due date moved from the pre-close snapshot)."""
import importlib.util
import json
import socket
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent
_SPEC = importlib.util.spec_from_file_location("did_fast_close_retry_test", HERE / "did-fast.py")
_df = importlib.util.module_from_spec(_SPEC)
sys.modules["did_fast_close_retry_test"] = _df
_SPEC.loader.exec_module(_df)  # type: ignore[union-attr]


class FakeTodoist:
    """Recurring daily task; a POST close always advances due server-side.
    `drop_first_post_response` makes the first POST raise socket.timeout
    AFTER the server has applied it (the real 2026-10-15 shape)."""

    def __init__(self, due="2026-10-15", drop_first_post_response=True, server_applies=True):
        self.due, self.posts, self.gets = due, 0, 0
        self.drop, self.server_applies = drop_first_post_response, server_applies

    def __call__(self, url, method="GET", timeout=15.0):
        if method == "POST":
            self.posts += 1
            if self.server_applies:
                y, m, d = map(int, self.due.split("-"))
                self.due = f"{y}-{m:02d}-{d + 1:02d}"
            if self.posts == 1 and self.drop:
                raise socket.timeout("timed out")
            return 204, b""
        self.gets += 1
        return 200, json.dumps({"id": "1", "checked": False,
                                "due": {"date": self.due, "is_recurring": True}}).encode()


class CloseRetryTests(unittest.TestCase):
    def _patch(self, fake):
        saved = _df._todoist_request
        _df._todoist_request = fake
        self.addCleanup(setattr, _df, "_todoist_request", saved)

    def test_timed_out_close_that_landed_is_not_retried(self):
        fake = FakeTodoist()
        self._patch(fake)
        tid, ok, err = _df.close_todoist_task("1", expected_due="2026-10-15")
        self.assertTrue(ok, err)
        self.assertEqual(fake.posts, 1, "retry re-closed the recurring task")
        self.assertEqual(fake.due, "2026-10-16")   # advanced exactly one day

    def test_timed_out_close_that_did_not_land_is_retried(self):
        fake = FakeTodoist(server_applies=False)
        # first POST times out and nothing changed server-side -> must retry;
        # make the retry succeed by letting the server apply from then on
        calls = {"n": 0}
        real = fake.__call__

        def flaky(url, method="GET", timeout=15.0):
            if method == "POST":
                calls["n"] += 1
                if calls["n"] == 1:
                    raise socket.timeout("timed out")
                fake.due = "2026-10-16"
                return 204, b""
            return real(url, method, timeout)
        self._patch(flaky)
        tid, ok, err = _df.close_todoist_task("1", expected_due="2026-10-15")
        self.assertTrue(ok, err)
        self.assertEqual(calls["n"], 2)

    def test_clean_close_posts_once(self):
        fake = FakeTodoist(drop_first_post_response=False)
        self._patch(fake)
        tid, ok, err = _df.close_todoist_task("1", expected_due="2026-10-15")
        self.assertTrue(ok, err)
        self.assertEqual(fake.posts, 1)
        self.assertEqual(fake.gets, 1)   # only the post-close verify, no pre-read

    def test_batch_passes_cached_due_through(self):
        """close_todoist_tasks hands each task's cached due to the retry check."""
        seen = {}

        def fake_close(tid, _retry=True, expected_due=None):
            seen[tid] = expected_due
            return tid, True, None
        saved = _df.close_todoist_task
        _df.close_todoist_task = fake_close
        self.addCleanup(setattr, _df, "close_todoist_task", saved)
        _df.close_todoist_tasks(["a", "b"], {"a": "2026-10-15"})
        self.assertEqual(seen, {"a": "2026-10-15", "b": None})


if __name__ == "__main__":
    unittest.main()
