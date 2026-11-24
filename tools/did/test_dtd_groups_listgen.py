#!/usr/bin/env python3
"""Task groups in the terminal dtd list (2026-11-23, lib/dtd_groups.py).

A Todoist parent with one level of subtasks renders as ONE row: its densest
due child plus a +N count; the parent row is hidden while a child shows; a
child is hidden only on positive evidence that its parent is not due. Runs
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


def test_group_shows_only_its_densest_child_with_a_count():
    tasks = [_task("P", "purchase l912 (240) [1360]"),
             _child("A", "agree funding accounts (10) [30]"),
             _child("B", "send Ticor reply (5) [40]"),
             _child("C", "send broker withdrawal (15) [30]"),
             _task("X", "unrelated (5) [5]")]
    out, order = _render(tasks)
    assert sorted(order) == ["B", "X"]
    row = next(l for l in out.splitlines() if l.endswith("\tB"))
    assert "+2" in row
    # the count sits ahead of the estimates, which stay right-justified
    assert row.index("+2") < row.index("(5)") < row.index("[40]")


def test_children_hidden_while_parent_is_due_later_or_closed():
    tasks = [_child("A", "x (5) [10]", parent="P1", parent_due="2026-08-20"),
             _child("B", "y (5) [10]", parent="P2", parent_closed=True),
             _task("X", "unrelated (5) [5]")]
    assert _render(tasks)[1] == ["X"]


def test_child_with_no_parent_stamps_still_shows():
    orphan = _task("A", "x (5) [10]")
    orphan["parent_id"] = "P"
    assert _render([orphan])[1] == ["A"]


def test_completed_child_cannot_win_the_row():
    """Regression guard: the densest child is already done (id in the removed
    list); its sibling must show, not vanish with it."""
    tasks = [_task("P", "outcome (240) [1360]"),
             _child("A", "funding (10) [30]"),
             _child("B", "ticor (5) [40]")]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        out = _run_listgen(tmp, {"updated": "t", "关键路径": [], "today": tasks})
        assert [i for i in _ids_in_order(out) if i in "PAB"] == ["B"]
        # re-run with B optimistically hidden
        (tmp / "removed.ids").write_text("B\n")
        payload = tmp / "lg.py"
        r = subprocess.run(
            ["python3", str(payload), str(tmp / "cache.json"), str(tmp / "done.json"),
             str(tmp / "removed"), TODAY, "120", str(tmp / "skipped"), str(tmp / "timer"),
             str(tmp / "view"), str(tmp / "blockpick")], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert [i for i in _ids_in_order(r.stdout) if i in "PAB"] == ["A"]


def test_recurring_parent_is_left_ungrouped():
    parent = _task("P", "weekly thing (30) [50]")
    parent["recurring"] = True
    tasks = [parent,
             _child("A", "x (5) [10]", parent_recurring=True),
             _child("B", "y (5) [20]", parent_recurring=True)]
    assert sorted(_render(tasks)[1]) == ["A", "B", "P"]


def test_payload_stays_safe_inside_the_zsh_double_quoted_string():
    """The generator is the body of python3 -c "..." in zsh: a double quote or
    backtick in it truncates or executes. The file-based runs above do not go
    through zsh, so pin it here."""
    body = _listgen_payload()
    assert '"' not in body
    assert "`" not in body
    assert DTD_PATH.exists()


BOX, ROBOT, BELL = "\U0001F4E6", "\U0001F916", "\U0001F514"


def _row(out, task_id):
    return next(l for l in out.splitlines() if l.endswith("\t" + task_id))


def test_outcome_row_is_marked_with_a_box_and_keeps_its_estimates():
    labelled = _task("L", "purchase l912 (240) [1360]")
    labelled["labels"] = ["m5x2", "grp"]
    out, order = _render([labelled, _task("X", "plain task (5) [5]")])
    assert sorted(order) == ["L", "X"]
    assert BOX in _row(out, "L") and BOX not in _row(out, "X")
    assert _row(out, "L").index("(240)") < _row(out, "L").index("[1360]")


def test_parent_whose_inputs_are_all_hidden_shows_as_a_marked_outcome():
    tasks = [_task("P", "outcome (60) [100]"),
             _child("A", "x (5) [10]", parent_closed=True)]
    out, order = _render(tasks)
    assert order == ["P"] and BOX in _row(out, "P")


def test_group_row_count_carries_the_box():
    tasks = [_task("P", "outcome (60) [100]"), _child("A", "a (10) [30]"), _child("B", "b (5) [40]")]
    out, order = _render(tasks)
    row = _row(out, "B")
    assert order == ["B"] and "+1" in row
    assert row.index(BOX) < row.index("+1") < row.index("(5)")   # the box leads the rollup row


def test_lone_input_still_carries_the_box():
    """JM 2026-11-23: a group with one due input showed no marker at all."""
    out, order = _render([_task("P", "outcome (60) [100]"), _child("A", "only input (30) [30]")])
    row = _row(out, "A")
    assert order == ["A"] and BOX in row and "+" not in row.split("only input")[1].split("(30)")[0]


def test_agent_input_is_folded_and_a_lone_one_shows_with_a_robot():
    agent = _child("A", "agent drafts (5) [90]")
    agent["labels"] = ["i9", "agent"]
    out, order = _render([_task("P", "outcome (60) [100]"), agent, _child("B", "send it (5) [20]")])
    assert order == ["B"] and "+" not in _row(out, "B").split("send it")[1].split("(5)")[0]
    out, order = _render([_task("P", "outcome (60) [100]"), agent])
    assert order == ["A"] and ROBOT in _row(out, "A")


def test_waiting_follow_up_shows_a_bell():
    w = _task("W", "follow up Jesus on calls (10) [8]")
    w["labels"] = ["m5x2", "waiting"]
    out, order = _render([w])
    assert order == ["W"] and BELL in _row(out, "W")
