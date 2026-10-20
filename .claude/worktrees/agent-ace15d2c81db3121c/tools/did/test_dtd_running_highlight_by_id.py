#!/usr/bin/env python3
"""Regression: dtd's "running" (▶) row highlight must match the started task
by id, not by its annotation-stripped name.

Bug (2026-07-19): "I started one AoS task, and it marked both as in
progress." Two Todoist tasks can share the same bare name after annotations
are stripped (e.g. a recurring "AoS (15) [15]" plus an unrelated one-off
"AoS (20) [8]" due later — the exact collision already known to bite dtd's
defer-hiding, see test_dtd_ritual_optimistic_hide.py's sibling history).
DTD_START wrote only `clean<TAB>epoch` to $DTD_TIMER, and the list generator
flagged EVERY row whose stripped name matched `clean` as running — so
starting either AoS task lit up both rows.

Fix: DTD_START also writes the started task's id as a 3rd field; the list
generator prefers an id match when present, falling back to the old
name-only match only for a timer file written before this fix.
"""
import json
import subprocess
import sys
from pathlib import Path

DTD_PATH = Path(__file__).resolve().parent / "dtd.sh"
DTD = DTD_PATH.read_text()


# ── Structural: DTD_START writes the id, list-gen prefers id matching ────────

def test_start_script_writes_task_id_as_third_field():
    i = DTD.index('DTD_START="/tmp/dtd-$DTD_ID.start.sh"')
    j = DTD.index("\nSTARTEOF", i)  # the CLOSING heredoc marker, not "<< STARTEOF"
    block = DTD[i:j]
    assert r'%s\t%s\t%s\t%s\n' in block, (
        "DTD_START must write a 4-field (name/epoch/id/project) timer line — "
        "field 3 = task id (running-row highlight), field 4 = project code "
        "(footer color, 2026-07-24)")
    assert '"\\$1" "\\$project" > "\\$TIMER"' in block, (
        "DTD_START must write the started task's id (the fzf row's resolved "
        "id, $1) as the timer file's 3rd field, or the list generator can't "
        "tell two same-named tasks apart")


def test_listgen_prefers_id_match_over_name_match():
    i = DTD.index("# Load running timer hint")
    assert "running_id = parts[2].strip() if len(parts) > 2 else ''" in DTD[i:i + 1200], (
        "list generator must parse the timer file's 3rd field (id)")
    k = DTD.index("if running_id:", i)
    assert "str(t.get('id', '')) == running_id" in DTD[k:k + 200]


# ── Functional: run the real list-gen payload and prove only the id'd row lights up ──

def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines))
              if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


def _run_listgen(tmp, cache_obj, timer_content):
    def _w(name, text):
        p = tmp / name
        p.write_text(text)
        return str(p)
    cache = _w("cache.json", json.dumps(cache_obj))
    done = _w("done.json", json.dumps({"date": "2026-07-19", "names": [], "ids": {}}))
    removed = _w("removed", "")
    (tmp / "removed.ids").write_text("")
    skipped = _w("skipped", "")
    timer = _w("timer", timer_content)
    view = _w("view", "")
    payload = tmp / "lg.py"
    payload.write_text(_listgen_payload())
    r = subprocess.run(
        [sys.executable, str(payload), cache, done, removed,
         "2026-07-19", "120", skipped, timer, view],
        capture_output=True, text=True)
    assert r.returncode == 0, f"list-gen crashed: {r.stderr}"
    return r.stdout


CACHE = {
    "updated": "2026-07-19T10:00:00",
    "today": [
        {"id": "AOS_RECUR", "content": "AoS (15) [15]", "labels": ["i9"],
         "priority": 1, "due": "2026-07-19", "recurring": True},
        {"id": "AOS_ONEOFF", "content": "AoS (20) [8]", "labels": ["i9"],
         "priority": 1, "due": "2026-07-19", "recurring": False},
    ],
}


def _row_for_id(output: str, task_id: str) -> str:
    for line in output.splitlines():
        if line.rsplit("\t", 1)[-1] == task_id:
            return line
    raise AssertionError(f"no row found for id {task_id!r} in:\n{output}")


def test_only_the_started_task_id_is_marked_running():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 3-field timer: name + epoch + the RECURRING task's id.
        timer_content = f"AoS\t{9999999999}\tAOS_RECUR"
        out = _run_listgen(tmp, CACHE, timer_content)

        recur_row = _row_for_id(out, "AOS_RECUR")
        oneoff_row = _row_for_id(out, "AOS_ONEOFF")

        assert "▶" in recur_row, "the task whose id matches the timer must show as running"
        assert "▶" not in oneoff_row, (
            "a different task that merely shares the same stripped name must "
            "NOT be marked running just because dtd started its namesake")


def test_legacy_two_field_timer_still_falls_back_to_name_match():
    # Back-compat: a timer file written before this fix (no id field) must
    # still highlight by name rather than highlighting nothing at all.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        timer_content = f"AoS\t{9999999999}"
        out = _run_listgen(tmp, CACHE, timer_content)
        recur_row = _row_for_id(out, "AOS_RECUR")
        oneoff_row = _row_for_id(out, "AOS_ONEOFF")
        # Documents the known (accepted) legacy limitation: without an id both
        # same-named rows still light up until the timer file is next
        # overwritten by the fixed DTD_START.
        assert "▶" in recur_row and "▶" in oneoff_row


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
