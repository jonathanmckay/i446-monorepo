#!/usr/bin/env python3
"""Regression: completing ONE of two same-named tasks must not hide both.

Bug (2026-07-24): "marked AoS as done in dtd, both visible AoS tasks
disappeared." Three tasks shared the content "AoS (15) [15]" (a recurring
1neon parent + two overdue one-off defer copies). Completion hid
optimistically by NAME ($REMOVED), and the list builder's name pass hides
every task whose clean name matches — so completing one copy suppressed all
of them for the rest of the session.

Fix: enter.sh/done.sh hide by id ($REMOVED.ids, the mechanism defers and
ritual cards already used), writing the name to $REMOVED only as an id-less
fallback. ctrl-z undo strips the id again: the worker passes the completed
fzf row id to `undo-fast --journal-done <journal> <task_id>`, which records
it in the journal as task_ids, and clean_filter_files removes those ids from
$REMOVED.ids on undo.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
DTD = (_HERE / "dtd.sh").read_text()


def _load_undo():
    spec = importlib.util.spec_from_file_location(
        "undo_fast", _HERE / "undo-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["undo_fast"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Structural: completions hide by id, name only as fallback ────────────────

def test_completion_script_hides_by_id_not_name():
    # done.sh (alt-enter) is the sole completion script (2026-07-31: enter.sh
    # dropped completion entirely -- "always opt+enter to mark done"). The
    # unconditional name-write is gone from it; the name lands in $REMOVED
    # only inside the id-less else-branch.
    assert DTD.count('echo "\\$1" >> "\\$REMOVED.ids"') == 1
    # Old pattern: name-write directly followed by the PUSHED/ids lines (i.e.
    # unconditional). New pattern: the name-write to $REMOVED sits in an
    # `else` branch after `if [[ -n "\$1" ]]`.
    start = DTD.index("<< DONEEOF")
    body = DTD[start:DTD.index("\nDONEEOF", start)]
    assert 'if [[ -n "\\$1" ]]; then' in body, "id-hide must be attempted first"
    nw = body.index('echo "\\$clean_for_filter" >> "\\$REMOVED"')
    assert body[:nw].rstrip().endswith("else"), \
        "name-write must be the id-less fallback only"


def test_enter_script_has_no_completion_hide_at_all():
    """enter.sh no longer completes anything (2026-07-31), so it must have
    none of the id/name hide machinery -- see also
    test_dtd_enter_never_completes.py for the fuller invariant."""
    start = DTD.index("<< ENTEREOF")
    body = DTD[start:DTD.index("\nENTEREOF", start)]
    assert "REMOVED" not in body


def test_worker_passes_task_id_to_journal_done():
    assert '--journal-done "$DTD_JOURNAL" "$task_id"' in DTD, (
        "the worker must pass the completed row id so undo can strip the "
        "id-hide")


# ── Functional: list builder hides exactly the completed duplicate ───────────

def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines))
              if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


CACHE = {
    "updated": "2026-07-24T10:00:00",
    "today": [
        {"id": "AOS1", "content": "AoS one (15) [15]", "labels": ["1neon", "xk88"],
         "priority": 1, "due": "2026-07-20", "recurring": False},
        {"id": "AOS2", "content": "AoS one (15) [15]", "labels": ["1neon", "xk88"],
         "priority": 1, "due": "2026-07-21", "recurring": False},
    ],
}


def _run_listgen(tmp, removed_ids_lines, removed_names=""):
    (tmp / "cache.json").write_text(json.dumps(CACHE))
    (tmp / "done.json").write_text(
        json.dumps({"date": "2026-07-24", "names": [], "ids": {}}))
    (tmp / "removed").write_text(removed_names)
    (tmp / "removed.ids").write_text("\n".join(removed_ids_lines))
    for n in ("skipped", "timer", "view"):
        (tmp / n).write_text("")
    payload = tmp / "lg.py"
    payload.write_text(_listgen_payload())
    r = subprocess.run(
        [sys.executable, str(payload), str(tmp / "cache.json"),
         str(tmp / "done.json"), str(tmp / "removed"), "2026-07-24", "120",
         str(tmp / "skipped"), str(tmp / "timer"), str(tmp / "view")],
        capture_output=True, text=True)
    assert r.returncode == 0, f"list-gen crashed: {r.stderr}"
    return r.stdout


def test_id_hide_leaves_same_named_sibling_visible(tmp_path):
    both = _run_listgen(tmp_path, [])
    assert both.count("AoS one") == 2, "both duplicates render before completion"

    one_done = _run_listgen(tmp_path, ["AOS1"])
    assert one_done.count("AoS one") == 1, (
        "completing one duplicate must hide exactly that one")


def test_name_hide_would_have_hidden_both(tmp_path):
    # Documents the bug shape the fix avoids: a name in $REMOVED nukes every
    # same-named task, which is why completions must not write it.
    via_name = _run_listgen(tmp_path, [], removed_names="aos one\n")
    assert "AoS one" not in via_name


# ── Undo: journal records the id, clean_filter_files strips it ───────────────

def test_journal_done_records_task_ids(tmp_path):
    journal = tmp_path / "undo.jsonl"
    out = {"results": [{"name": "AoS one",
                        "todoist": {"id": "AOS1", "closed": True}}]}
    r = subprocess.run(
        [sys.executable, str(_HERE / "undo-fast.py"),
         "--journal-done", str(journal), "AOS1"],
        input=json.dumps(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rec = json.loads(journal.read_text().strip())
    assert rec["task_ids"] == ["AOS1"]


def test_clean_filter_files_strips_task_ids_from_removed_ids(tmp_path):
    uf = _load_undo()
    removed = tmp_path / "removed"
    removed.write_text("")
    (tmp_path / "removed.ids").write_text("AOS1\nOTHER\n")
    uf.clean_filter_files(["AoS one"], None, str(removed), None,
                          task_ids=["AOS1"])
    kept = (tmp_path / "removed.ids").read_text().split()
    assert kept == ["OTHER"], "undo must strip only the undone task's id"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
