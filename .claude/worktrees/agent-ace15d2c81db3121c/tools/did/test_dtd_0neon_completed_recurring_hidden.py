#!/usr/bin/env python3
"""Regression: a completed recurring 0neon/夜neon habit must not linger in dtd.

Bug (2026-08-02, user report: "0neon tasks are not getting cleared from dtd").
dtd's live list generator (dtd.sh's embedded list-gen payload) bounds the
0neon/夜neon sections to due<=TOMORROW so a just-completed recurring habit
(whose due date advances +1 day on close) is still a candidate row — the
intent (per the surrounding comment, mirroring dtd.py's already-correct
zeroneon guard) is to then EXCLUDE it because it's recurring and due is in
the future. The actual filter was:

    (t.get('recurring', True) or t['due'] <= today)

`recurring` defaults to True and a genuinely recurring task's cache entry
also has `recurring: True`, so for every recurring 0neon/夜neon row this
clause reduces to `(True or ...)` — always true. The due-date exclusion was
therefore a complete no-op for recurring cards: a just-completed habit
(due=tomorrow) was never filtered out and stayed visible in dtd indefinitely
(until, at best, the next day's cache regenerated it further past the bound).

Fix: `not (t.get('recurring', True) and t['due'] > today)` — matching
dtd.py's separate, already-correct `if recurring and due>today: continue`
guard. A non-recurring deferred one-off copy is unaffected (its own bound is
plain due<=today, independent of the recurring exclusion).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DTD_PATH = Path(__file__).resolve().parent / "dtd.sh"
DTD = DTD_PATH.read_text()

TODAY = "2026-08-02"
TOMORROW = "2026-08-03"


def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines))
              if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


def _run_listgen(tmp, cache_obj):
    def _w(name, text):
        p = tmp / name
        p.write_text(text)
        return str(p)
    cache = _w("cache.json", json.dumps(cache_obj))
    done = _w("done.json", json.dumps({"date": TODAY, "names": [], "ids": {}}))
    removed = _w("removed", "")
    (tmp / "removed.ids").write_text("")
    skipped = _w("skipped", "")
    timer = _w("timer", "")
    view = _w("view", "")
    payload = tmp / "lg.py"
    payload.write_text(_listgen_payload())
    r = subprocess.run(
        [sys.executable, str(payload), cache, done, removed,
         TODAY, "120", skipped, timer, view],
        capture_output=True, text=True)
    assert r.returncode == 0, f"list-gen crashed: {r.stderr}"
    return r.stdout


def _cache(due_xk20: str, recurring_xk20: bool = True):
    return {
        "updated": f"{TODAY}T10:00:00",
        "today": [],
        "0neon": [
            {"id": "XK20", "content": "xk20 (30) [35]",
             "labels": ["0neon", "xk87"], "priority": 3,
             "due": due_xk20, "recurring": recurring_xk20},
            {"id": "OL1", "content": "0l (8) [20]",
             "labels": ["0neon", "g245"], "priority": 1,
             "due": TODAY, "recurring": True},
        ],
    }


def test_completed_recurring_habit_hidden_once_due_advances(tmp_path):
    """The core bug: xk20 closed today → due advances to tomorrow → must vanish."""
    out = _run_listgen(tmp_path, _cache(due_xk20=TOMORROW))
    assert "xk20" not in out, (
        "a recurring 0neon habit due tomorrow (i.e. just completed today) "
        "must not still render in dtd's list"
    )
    assert "0l" in out, "an unrelated, not-yet-done habit must still render"


def test_not_yet_done_recurring_habit_still_shown(tmp_path):
    """Sanity check: due=today (not yet completed) must still render."""
    out = _run_listgen(tmp_path, _cache(due_xk20=TODAY))
    assert "xk20" in out, "a recurring habit still due today must render"


def test_non_recurring_deferred_copy_due_tomorrow_stays_hidden(tmp_path):
    """The recurring-only exclusion must not accidentally show a deferred
    one-off copy before it's actually due (bug 2026-07-21, guarded elsewhere)."""
    out = _run_listgen(tmp_path, _cache(due_xk20=TOMORROW, recurring_xk20=False))
    assert "xk20" not in out, (
        "a non-recurring 0neon copy not yet due must stay hidden"
    )
