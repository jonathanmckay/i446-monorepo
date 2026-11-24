"""Regression test: a recurring card whose Todoist close never landed must be
closable by a same-day re-run.

Bug 2026-11-23/24 ("1v is a 1n task I completed yesterday... why do I see it
now?"): dtd on Straylight completed "1 vm+li+msgr" at 13:55. The 1n+ cell and
the 0分 credit landed, the Todoist close did not, and did-fast still recorded
the NAME in completed-today (no id) and exited 0. The 18:56 re-run then hit
the Step 6 guard, "already done today (skipped re-close...)", which trusted
the name alone, so the card stayed open and came back the next morning still
due 11-23.

The guard now skips only when the close is known to have landed: the id is in
completed-today's ids, or the live task's due date has moved past today.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DID_FAST = HERE / "did-fast.py"

_SPEC = importlib.util.spec_from_file_location("did_fast_reclose", DID_FAST)
df = importlib.util.module_from_spec(_SPEC)
sys.modules["did_fast_reclose"] = df
_SPEC.loader.exec_module(df)  # type: ignore[union-attr]

TID = "6gJPwcFvxmPRmFvW"
TODAY = "2026-11-23"
CARD = {"id": TID, "content": "1 vm+li+msgr (10) [20]", "labels": ["1neon", "i9"],
        "due": TODAY, "due_string": "every Monday", "recurring": True}


def _fetch(due):
    calls = []

    def fetch(tid):
        calls.append(tid)
        return None if due is None else {**CARD, "due": due}
    return fetch, calls


def test_name_recorded_without_id_and_still_due_today_is_reclosed():
    """The 11-23 case: name in completed-today, id absent, Todoist still due
    today. The close is owed."""
    fetch, _ = _fetch(TODAY)
    live = df._reclose_owed(CARD, set(), TODAY, fetch=fetch)
    assert live is not None and live["due"] == TODAY


def test_overdue_card_whose_close_never_landed_is_reclosed():
    fetch, _ = _fetch("2026-11-16")
    assert df._reclose_owed(CARD, set(), TODAY, fetch=fetch) is not None


def test_recurrence_already_advanced_is_not_reclosed():
    """The drift class the guard exists for (2026-06-27, 0t): the close
    landed, the due date moved on, a second close would skip an occurrence."""
    fetch, _ = _fetch("2026-11-30")
    assert df._reclose_owed(CARD, set(), TODAY, fetch=fetch) is None


def test_id_recorded_as_closed_is_not_reclosed_and_not_fetched():
    fetch, calls = _fetch(TODAY)
    assert df._reclose_owed(CARD, {TID}, TODAY, fetch=fetch) is None
    assert calls == []


def test_unreadable_task_is_not_reclosed():
    fetch, _ = _fetch(None)
    assert df._reclose_owed(CARD, set(), TODAY, fetch=fetch) is None


def test_timed_due_is_compared_by_date():
    fetch, _ = _fetch(TODAY + "T09:00:00")
    live = df._reclose_owed(CARD, set(), TODAY, fetch=fetch)
    assert live is not None and live["due"] == TODAY


def test_guard_consults_reclose_owed_before_skipping():
    src = DID_FAST.read_text()
    skip_pos = src.index("mc._normalize(r.item.name) in done_today")
    close_pos = src.index("close_todoist_tasks(task_ids, due_by_id)")
    assert "_reclose_owed(" in src[skip_pos:close_pos]


# ── A failed close must fail the run, where a retry is safe ─────────────────
# JM 2026-11-24: "yes" to dtd noticing a missed close and retrying by itself,
# but only for done/not-done cards: a retry of a minutes habit (xk20, 问学,
# per-minute weekly cards) would count the minutes twice.

def _result(step, tid=TID, **kw):
    item = df.ParsedItem(raw="x", name=kw.pop("name", "1 vm+li+msgr"),
                         time_value=kw.pop("time_value", None))
    item.time_range = kw.pop("time_range", None)
    r = df.RouteResult(item=item, step=step, **kw)
    r.todoist_task = {"id": tid} if tid else None
    return r


def test_failed_close_of_binary_1n_card_is_reported():
    r = _result("1n")
    got = df._retryable_close_failures([r], {TID: (False, "HTTP 429: slow down")})
    assert got == [("1 vm+li+msgr", TID, "HTTP 429: slow down")]


def test_failed_close_of_plain_task_is_reported():
    r = _result("todoist", name="call dentist")
    assert df._retryable_close_failures([r], {TID: (False, "timeout")})


def test_ok_close_and_unattempted_close_are_not_failures():
    r = _result("1n")
    assert df._retryable_close_failures([r], {TID: (True, None)}) == []
    assert df._retryable_close_failures([r], {}) == []   # guard skip, not a failure


def test_minutes_habits_are_never_retried():
    fail = {TID: (False, "timeout")}
    assert df._retryable_close_failures([_result("0n", name="xk20")], fail) == []
    assert df._retryable_close_failures([_result("1n", is_variable_1n=True)], fail) == []
    assert df._retryable_close_failures([_result("1n", is_cumulative_1n=True)], fail) == []
    assert df._retryable_close_failures([_result("1n", time_value=30)], fail) == []
    assert df._retryable_close_failures(
        [_result("todoist", time_range=("0900", "0930"))], fail) == []


def test_close_failure_block_makes_the_run_exit_nonzero():
    out = {"results": [], "todoist_close_write": {"ok": False, "error": "x"}}
    assert df._write_failures(out) == ["todoist_close_write: x"]
    src = DID_FAST.read_text()
    assert 'output["todoist_close_write"]' in src


def test_rate_limit_is_transient():
    import io
    import urllib.error
    e = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(b""))
    assert df._classify_error(e)[1] is True
    e = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b""))
    assert df._classify_error(e)[1] is False


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
