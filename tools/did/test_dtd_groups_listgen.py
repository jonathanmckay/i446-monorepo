#!/usr/bin/env python3
"""Task groups in the terminal dtd list (2026-11-23, lib/dtd_groups.py).

A Todoist parent with one level of subtasks renders as ONE block: the outcome
row with a box, then its due inputs indented beneath it, densest first; an
input is hidden only on positive evidence that its parent is not due. Runs
the real list generator extracted from dtd.sh.
"""
import subprocess
import tempfile
from pathlib import Path

from test_dtd_stable_render_order import (DTD_PATH, _ids_in_order, _listgen_payload,
                                          _run_listgen, _task)

TODAY = "2026-08-06"


def _child(task_id, content, parent="P", **stamps):
    t = _task(task_id, content)
    t.update(parent_id=parent, parent_due=TODAY, parent_content="purchase l912 [1360]",
             parent_recurring=False, parent_closed=False)
    t.update(stamps)
    return t


def _render(tasks):
    with tempfile.TemporaryDirectory() as d:
        out = _run_listgen(Path(d), {"updated": "t", "关键路径": [], "today": tasks})
    ids = {t["id"] for t in tasks}
    return out, [i for i in _ids_in_order(out) if i in ids]


BOX, ROBOT, BELL, CORNER = "\U0001F4E6", "\U0001F916", "\U0001F514", "\u2514"


def _row(out, task_id):
    return next(l for l in out.splitlines() if l.endswith("\t" + task_id))


def _all_ids(out):
    return _ids_in_order(out)


def test_group_is_one_block_outcome_then_inputs_densest_first():
    tasks = [_task("P", "purchase l912 (240) [1360]"),
             _child("A", "agree funding accounts (10) [30]"),
             _child("B", "send Ticor reply (5) [40]"),
             _child("C", "send broker withdrawal (15) [30]"),
             _task("X", "unrelated (5) [5]")]
    out, order = _render(tasks)
    i = order.index("P")
    assert order[i:i + 4] == ["P", "B", "A", "C"]            # contiguous, densest first
    assert BOX in _row(out, "P") and BOX not in _row(out, "X")
    for c in "ABC":
        assert CORNER in _row(out, c) and BOX not in _row(out, c)
    assert CORNER not in _row(out, "X")
    # estimates stay at the right edge on input rows (the box is two cells
    # wide, so only same-prefix rows are compared by length)
    ends = {len(_row(out, c).rsplit("\t", 1)[0]) for c in "BAC"}
    assert len(ends) == 1 and "(5) [40]" in _row(out, "B")


def test_lone_input_sits_under_its_boxed_outcome():
    out, order = _render([_task("P", "outcome (60) [100]"), _child("A", "only input (30) [30]")])
    assert order == ["P", "A"] and BOX in _row(out, "P") and CORNER in _row(out, "A")


def test_children_hidden_while_parent_is_due_later_or_closed():
    tasks = [_child("A", "x (5) [10]", parent="P1", parent_due="2026-08-20"),
             _child("B", "y (5) [10]", parent="P2", parent_closed=True),
             _task("X", "unrelated (5) [5]")]
    assert _render(tasks)[1] == ["X"]


def test_child_with_no_parent_stamps_still_shows():
    orphan = _task("A", "x (5) [10]")
    orphan["parent_id"] = "P"
    assert _render([orphan])[1] == ["A"]


def test_completed_input_leaves_the_rest_of_the_block():
    tasks = [_task("P", "outcome (240) [1360]"),
             _child("A", "funding (10) [30]"),
             _child("B", "ticor (5) [40]")]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        out = _run_listgen(tmp, {"updated": "t", "关键路径": [], "today": tasks})
        assert [i for i in _ids_in_order(out) if i in "PAB"] == ["P", "B", "A"]
        (tmp / "removed.ids").write_text("B\n")      # B optimistically hidden
        r = subprocess.run(
            ["python3", str(tmp / "lg.py"), str(tmp / "cache.json"), str(tmp / "done.json"),
             str(tmp / "removed"), TODAY, "120", str(tmp / "skipped"), str(tmp / "timer"),
             str(tmp / "view"), str(tmp / "blockpick")], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert [i for i in _ids_in_order(r.stdout) if i in "PAB"] == ["P", "A"]


def test_recurring_parent_is_left_ungrouped():
    parent = _task("P", "weekly thing (30) [50]")
    parent["recurring"] = True
    tasks = [parent,
             _child("A", "x (5) [10]", parent_recurring=True),
             _child("B", "y (5) [20]", parent_recurring=True)]
    out, order = _render(tasks)
    assert sorted(order) == ["A", "B", "P"] and CORNER not in out and BOX not in out


def test_outcome_with_no_due_inputs_is_a_boxed_row():
    labelled = _task("L", "purchase l912 (240) [1360]")
    labelled["labels"] = ["m5x2", "grp"]
    hidden_kid = [_task("P", "outcome (60) [100]"), _child("A", "x (5) [10]", parent_closed=True)]
    out, order = _render([labelled, _task("X", "plain task (5) [5]")] + hidden_kid)
    assert sorted(order) == ["L", "P", "X"]
    assert BOX in _row(out, "L") and BOX in _row(out, "P") and BOX not in _row(out, "X")


def test_agent_input_is_dimmed_last_and_waiting_shows_a_bell():
    agent = _child("A", "agent drafts (5) [90]")
    agent["labels"] = ["i9", "agent"]
    out, order = _render([_task("P", "outcome (60) [100]"), agent, _child("B", "send it (5) [20]")])
    assert order == ["P", "B", "A"] and ROBOT in _row(out, "A") and CORNER in _row(out, "A")
    w = _task("W", "follow up Jesus on calls (10) [8]")
    w["labels"] = ["m5x2", "waiting"]
    out, order = _render([w])
    assert order == ["W"] and BELL in _row(out, "W")


def test_payload_stays_safe_inside_the_zsh_double_quoted_string():
    """The generator is the body of python3 -c "..." in zsh: a double quote or
    backtick in it truncates or executes. The file-based runs above do not go
    through zsh, so pin it here."""
    body = _listgen_payload()
    assert '"' not in body
    assert "`" not in body
    assert DTD_PATH.exists()
