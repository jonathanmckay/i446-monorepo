"""Regression test: did-fast must not re-close a recurring habit already
completed today.

Each Todoist close advances an "every day" recurrence by a day, so completing
the same daily habit twice in a day drifts its due date past today and it
silently vanishes from dtd (2026-06-27: 0t drifted to due-tomorrow). The guard
uses completed-today.json (authoritative for "done today") rather than the
cached due date, which can lag Todoist.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
DID_FAST = HERE / "did-fast.py"


# ── Structural: the guard exists and is wired correctly ─────────────────────

def test_guard_reads_completed_today_date_gated():
    src = DID_FAST.read_text()
    assert "mc._load(mc.COMPLETED)" in src, "guard must read completed-today via mc"
    assert '_ct.get("date") == today_str' in src, "done_today must be date-gated"
    assert "done_today" in src


def test_guard_skips_recurring_already_done():
    """The close loop must `continue` (skip close) for a recurring task whose
    normalized name is already in done_today — before it reaches close."""
    src = DID_FAST.read_text()
    # The skip predicate and its continue.
    assert 'r.todoist_task.get("recurring")' in src
    assert "mc._normalize(r.item.name) in done_today" in src
    # The skip must precede the close call (close_todoist_tasks) in source.
    skip_pos = src.index("in done_today")
    close_pos = src.index("close_todoist_tasks(task_ids)")
    assert skip_pos < close_pos, "idempotency skip must run before the Todoist close"
    # And it routes through future_skipped so callers see the warning.
    seg = src[skip_pos:close_pos]
    assert "future_skipped.append" in seg and "continue" in seg


# ── Functional: the predicate the loop relies on behaves correctly ──────────

def _load_mc():
    spec = importlib.util.spec_from_file_location("mark_completed", HERE / "mark-completed.py")
    mc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mc)
    return mc


def test_done_today_predicate(tmp_path, monkeypatch):
    mc = _load_mc()
    today_str = date.today().isoformat()
    f = tmp_path / "completed-today.json"

    # Same-day file with 0t already completed.
    f.write_text(json.dumps({"date": today_str, "names": ["0t", "ibx s897"]}))
    ct = mc._load(f)
    done = ({mc._normalize(n) for n in ct.get("names", [])}
            if ct.get("date") == today_str else set())
    assert mc._normalize("0t") in done          # recurring habit already done → would skip
    assert mc._normalize("hiit") not in done     # not done → would close normally

    # Stale (yesterday's) file must NOT suppress today: date gate yields empty.
    f.write_text(json.dumps({"date": "2020-01-01", "names": ["0t"]}))
    ct = mc._load(f)
    done = ({mc._normalize(n) for n in ct.get("names", [])}
            if ct.get("date") == today_str else set())
    assert done == set(), "stale completed-today must not gate today's completions"


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
