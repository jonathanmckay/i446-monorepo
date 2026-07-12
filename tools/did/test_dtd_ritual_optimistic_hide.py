#!/usr/bin/env python3
"""Regression: completing a ritual (-1neon) card hides it IMMEDIATELY, not after
the ~7s worker+refresh.

Bug (2026-07-12): "the -1n tasks disappear for about 7 seconds whenever I
complete a task." Normal tasks are optimistically hidden the instant they're
completed — their name goes into $REMOVED and the list builder filters by name.
But ritual cards are deliberately name-EXEMPT from that hide (2026-07-10 fix, so
a completed card can't suppress the next block's identically-named card), so a
ritual is hidden ONLY by id — and its id doesn't reach the overlay until the
worker runs run_ritual + a blocking refresh_task_queue (~7s). So every other
task vanished instantly while a -1n card lingered ~7s before disappearing.

Fix: enter.sh/done.sh write the completed card's id (the {2} id field) to
$REMOVED.ids — gated to ritual cards (name carries 😈) so a normal task's ctrl-z
undo (which reopens by clearing $REMOVED, not $REMOVED.ids) still works — and the
list builder hides any cache row whose id is in that file, right alongside the
daemon-overlay id hide.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DTD_PATH = Path(__file__).resolve().parent / "dtd.sh"
DTD = DTD_PATH.read_text()


# ── Structural: the write scripts record the id, gated to rituals ─────────────

def test_enter_and_done_write_ritual_id_gated_on_marker():
    # Both completion scripts must append the id to $REMOVED.ids, and ONLY for
    # ritual cards (name carries 😈) — never for normal tasks (whose undo relies
    # on $REMOVED name-clearing and can't clear the id file).
    # dtd.sh's enter/done scripts live in unquoted heredocs, so the source keeps
    # the `\$` escaping (`\$clean`, `\$1`, `\$REMOVED`).
    assert DTD.count('*😈* ]] && echo "\\$1" >> "\\$REMOVED.ids"') == 2, (
        "enter.sh (complete branch) and done.sh must each write the ritual id, "
        "gated on the 😈 marker")


def test_listgen_loads_and_applies_removed_ids():
    assert "removed_file + '.ids'" in DTD, "list builder must load $REMOVED.ids"
    assert "str(t['id']) in removed_ids" in DTD, (
        "list builder must hide a cache row whose id is in removed_ids")


def test_removed_ids_file_is_cleaned_up():
    # Stale-file hygiene: removed at startup and on exit.
    assert '"/tmp/dtd-$DTD_ID.removed.ids"' in DTD, "startup rm must clear removed.ids"
    assert '"$DTD_REMOVED.ids"' in DTD, "exit cleanup must remove removed.ids"


# ── Functional: run the real list-gen payload and prove the id hides ─────────

def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines))
              if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


def _run_listgen(tmp, cache_obj, removed_ids_lines):
    def _w(name, text):
        p = tmp / name
        p.write_text(text)
        return str(p)
    import json
    cache = _w("cache.json", json.dumps(cache_obj))
    done = _w("done.json", json.dumps({"date": "2026-07-12", "names": [], "ids": {}}))
    removed = _w("removed", "")
    # The sibling id file the list builder reads: removed_file + '.ids'
    (tmp / "removed.ids").write_text("\n".join(removed_ids_lines))
    skipped = _w("skipped", "")
    timer = _w("timer", "")
    view = _w("view", "")
    payload = tmp / "lg.py"
    payload.write_text(_listgen_payload())
    r = subprocess.run(
        [sys.executable, str(payload), cache, done, removed,
         "2026-07-12", "120", skipped, timer, view],
        capture_output=True, text=True)
    assert r.returncode == 0, f"list-gen crashed: {r.stderr}"
    return r.stdout


CACHE = {
    "updated": "2026-07-12T10:00:00",
    "today": [
        {"id": "RIT1", "content": "😈 -1g", "labels": ["-1neon"],
         "priority": 1, "due": "2026-07-12", "recurring": False},
        {"id": "NORM1", "content": "some normal task [10]", "labels": ["i9"],
         "priority": 1, "due": "2026-07-12", "recurring": False},
    ],
}


def test_ritual_hidden_when_id_in_removed_ids(tmp_path):
    # Without the id → ritual renders. With the id → ritual is gone; the normal
    # task is untouched.
    before = _run_listgen(tmp_path, CACHE, [])
    assert "-1g" in before, "ritual should render when its id is not optimistically removed"

    after = _run_listgen(tmp_path, CACHE, ["RIT1"])
    assert "-1g" not in after, "ritual must be hidden the instant its id is in removed.ids"
    assert "some normal task" in after, "the id hide must not touch other tasks"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
