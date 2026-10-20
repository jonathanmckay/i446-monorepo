"""Regression test: todoist-auto-triage.py must not mangle -1neon ritual cards.

Bug 2026-07-30 (mechanism 2 of the -1t/-1l double-creation bug):
todoist-auto-triage.py runs every 2h over the same inbox project the
-1neon ritual cards (سمش/-1g/-1ibx/-1t/-1l, created by
build-order-daemon.py) live in, and appended a default "(15) [15]" to
their bare "😈 <tag>" content since it had no exclusion for them. Real
ritual cards never carry [N] (their points come from 0分!P via the block
header) -- and that mutated content is exactly what build-order-daemon's
dedup check (_ritual_bare_tag) matches on, so repeatedly rewriting it
contributed to the duplicate-card bug. Confirmed via Todoist find-activity:
task 6h9J84mjfWfgP38c ("😈 -1t") added 2026-07-30T13:00:35Z by the daemon,
then mutated to "😈 -1t (15) [15]" at 2026-07-30T14:00:26Z by auto-triage's
python-requests client.
"""
import importlib.util
from pathlib import Path
from unittest.mock import patch

TRIAGE = Path(__file__).parent / "todoist-auto-triage.py"


def _load_triager():
    spec = importlib.util.spec_from_file_location("todoist_auto_triage", TRIAGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.TodoistTriager(api_token="tok", dry_run=True, verbose=False)


def test_ritual_card_is_skipped_untouched():
    triager = _load_triager()
    task = {"id": "6h9J84mjfWfgP38c", "content": "😈 -1t", "labels": ["-1neon"],
            "priority": 1, "due": None}
    with patch("requests.post") as post, patch("requests.get") as get:
        triager._process_task(task)
    post.assert_not_called()
    get.assert_not_called()
    assert triager.stats["processed"] == 0


def test_non_ritual_task_still_gets_estimated():
    """Sanity check the skip is scoped to the -1neon label, not a blanket
    no-op that would also silently stop triaging real inbox tasks."""
    triager = _load_triager()
    task = {"id": "999", "content": "write quarterly report", "labels": [],
            "priority": 1, "due": None}
    triager._process_task(task)
    assert triager.stats["processed"] == 1
