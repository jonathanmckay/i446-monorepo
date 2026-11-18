"""Regression test: run.py closed "1 xk87 wknd" when asked to close "1 xk87".

Bug 2026-11-17 ("I did the 1xk887 survey, but it only marked xk87 as done,
also ... 1 xk87 weekend plans ... didn't flow through to neon"): the survey's
end-of-form `run.py "1 xk87"` scored 1.0 word overlap against both weekly
cards, kept the first one Todoist listed (the weekend card), closed it, and
wrote Neon for 1 xk87. The 1 xk87 card stayed open and the weekend card was
closed with no Neon credit.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

_SPEC = importlib.util.spec_from_file_location("did_run_exact_name", _HERE / "run.py")
run = importlib.util.module_from_spec(_SPEC)
sys.modules["did_run_exact_name"] = run
_SPEC.loader.exec_module(run)  # type: ignore[union-attr]

WKND = {"id": "WKND", "content": "1 xk87 wknd (20) [20]", "due": {"date": "2026-11-17"}}
XK87 = {"id": "XK87", "content": "1 xk87 (20) [45]", "due": {"date": "2026-11-17"}}
XK88 = {"id": "XK88", "content": "1 xk88 (5) [15]", "due": {"date": "2026-11-17"}}


class _FakeTodoist:
    def __init__(self, tasks):
        self.tasks, self.closed = tasks, []

    def find_tasks(self, **kw):
        return list(self.tasks)

    def close_task(self, tid):
        self.closed.append(tid)


def _close(query, tasks):
    fake = _FakeTodoist(tasks)
    with patch.object(run, "todoist", fake), patch.object(run, "_drop_from_queue", lambda _id: None):
        content, tid = run._find_and_close_todoist("1neon", query, [])
    return tid, fake.closed


def test_exact_name_beats_longer_card_listed_first():
    tid, closed = _close("1 xk87", [WKND, XK88, XK87])
    assert tid == "XK87" and closed == ["XK87"]


def test_exact_name_still_wins_when_listed_first():
    tid, closed = _close("1 xk87", [XK87, WKND])
    assert tid == "XK87" and closed == ["XK87"]


def test_longer_name_still_closes_its_own_card():
    tid, closed = _close("1 xk87 wknd", [XK87, WKND])
    assert tid == "WKND" and closed == ["WKND"]


def test_annotations_do_not_count_as_extra_words():
    assert run._extra_words("1 xk87", "1 xk87 (20) [45]") == 0
    assert run._extra_words("1 xk87", "1 xk87 wknd (20) [20]") == 1
