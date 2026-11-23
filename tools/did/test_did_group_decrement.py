#!/usr/bin/env python3
"""Task groups, value half (2026-11-23, lib/dtd_groups.py): paying a subtask
takes the same amount off its parent's remaining [N]; closing the parent pays
the LIVE remainder; undo gives back exactly what was taken."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, _HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def df():
    return _load("did_fast_groups", "did-fast.py")


class Todoist:
    def __init__(self, **tasks):
        self.tasks = tasks
        self.updates = []

    def get(self, i):
        t = self.tasks.get(i)
        return dict(t) if t else None

    def update(self, i, body):
        self.updates.append((i, dict(body)))
        self.tasks[i].update(body)


def _r(name, task, pts, extra=()):
    return SimpleNamespace(item=SimpleNamespace(name=name), todoist_task=task,
                           fen_points=pts, fen_extra=list(extra))


def test_paid_child_comes_off_the_parent_and_record_reaches_the_output(df, tmp_path):
    td = Todoist(P={"content": "purchase l912 (240) [1360]", "labels": ["m5x2"]})
    fast = [_r("send ticor reply", {"id": "C", "parent_id": "P"}, 40),
            _r("unrelated", {"id": "X"}, 5)]
    recs = df._group_decrements(fast, {"C": (True, None), "X": (True, None)}, {}, set(),
                                "2026-11-23", journal=tmp_path / "j",
                                get_task=td.get, update_task=td.update)
    assert td.tasks["P"]["content"] == "purchase l912 (240) [1320]"
    assert "grp" in td.tasks["P"]["labels"]
    assert recs == {"C": {"parent": "P", "key": "C:2026-11-23", "taken": [[40, None]],
                          "parent_content": "purchase l912 (240) [1320]"}}


def test_retry_does_not_take_twice(df, tmp_path):
    td = Todoist(P={"content": "o [100]", "labels": ["grp"]})
    fast = [_r("c", {"id": "C", "parent_id": "P"}, 30)]
    kw = dict(journal=tmp_path / "j", get_task=td.get, update_task=td.update)
    df._group_decrements(fast, {"C": (True, None)}, {}, set(), "2026-11-23", **kw)
    assert df._group_decrements(fast, {"C": (True, None)}, {}, set(), "2026-11-23", **kw) == {}
    assert td.tasks["P"]["content"] == "o [70]"


def test_nothing_taken_when_points_did_not_land_or_close_failed(df, tmp_path):
    td = Todoist(P={"content": "o [100]", "labels": ["grp"]})
    kw = dict(journal=tmp_path / "j", get_task=td.get, update_task=td.update)
    fast = [_r("c", {"id": "C", "parent_id": "P"}, 30)]
    assert df._group_decrements(fast, {"C": (True, None)}, {}, {"c"}, "2026-11-23", **kw) == {}
    assert df._group_decrements(fast, {"C": (False, "500")}, {}, set(), "2026-11-23", **kw) == {}
    assert td.updates == []


def test_split_segments_of_one_task_are_summed_into_one_decrement(df, tmp_path):
    td = Todoist(P={"content": "o [100]", "labels": ["grp"]})
    task = {"id": "C", "parent_id": "P"}
    fast = [_r("c", task, 10, [("R", 5)]), _r("c", task, 20)]
    df._group_decrements(fast, {"C": (True, None)}, {}, set(), "2026-11-23",
                         journal=tmp_path / "j", get_task=td.get, update_task=td.update)
    assert td.tasks["P"]["content"] == "o [65]"
    assert len(td.updates) == 1


def test_partial_defer_takes_what_was_paid_and_leaves_the_close_free(df, tmp_path):
    td = Todoist(P={"content": "o [100]", "labels": ["grp"]})
    kw = dict(journal=tmp_path / "j", get_task=td.get, update_task=td.update)
    fast = [_r("c", {"id": "C", "parent_id": "P"}, 10)]
    df._group_decrements(fast, {}, {"C": (True, None)}, set(), "2026-11-23", **kw)
    df._group_decrements(fast, {"C": (True, None)}, {}, set(), "2026-11-23", **kw)
    assert td.tasks["P"]["content"] == "o [80]"


def test_closing_a_group_parent_pays_the_live_remainder(df, monkeypatch):
    monkeypatch.setattr(df, "_fetch_task_by_id",
                        lambda i: {"id": i, "content": "purchase l912 (240) [1220]"})
    item = SimpleNamespace(project_override=None, points_split=None, points_override=None,
                           defer_date=None)
    stale = {"id": "P", "content": "purchase l912 (240) [1360]", "labels": ["m5x2", "grp"]}
    pts, _col, _extra = df._task_credit(item, stale)
    assert pts == 1220 and stale["content"].endswith("[1220]")


def test_ordinary_task_never_triggers_a_live_read(df, monkeypatch):
    def boom(_i):
        raise AssertionError("no fetch for a task without the group label")
    monkeypatch.setattr(df, "_fetch_task_by_id", boom)
    item = SimpleNamespace(project_override=None, points_split=None, points_override=None,
                           defer_date=None)
    assert df._task_credit(item, {"id": "T", "content": "x (5) [7]", "labels": ["m5x2"]})[0] == 7


def test_undo_gives_the_parent_back_what_the_completion_took(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sys.modules.pop("state_paths", None)
    import dtd_groups
    import state_paths
    td = Todoist(P={"content": "o [100]", "labels": ["grp"]})
    journal = state_paths.STATE_DIR / "group-decrements.jsonl"
    dtd_groups.decrement_parent("C", "P", 30, "2026-11-23", journal=journal,
                                get_task=td.get, update_task=td.update)
    assert td.tasks["P"]["content"] == "o [70]"
    uf = _load("undo_fast_groups", "undo-fast.py")
    monkeypatch.setattr(uf, "_api", lambda method, path, body=None, timeout=15.0:
                        td.get(path.rsplit("/", 1)[-1]) if method == "GET"
                        else td.update(path.rsplit("/", 1)[-1], body))
    errors = []
    uf.reverse_didfast_output(
        {"results": [{"name": "c", "group": {"parent": "P", "key": "C:2026-11-23",
                                             "taken": [[30, None]]}}]},
        "11/23", "2026-11-23", errors)
    assert errors == [] and td.tasks["P"]["content"] == "o [100]"
    sys.modules.pop("state_paths", None)


def test_undo_of_a_split_restores_the_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sys.modules.pop("state_paths", None)
    import dtd_groups
    import state_paths
    td = Todoist(P={"content": "o [100]", "labels": ["grp"]},
                 C={"content": "x (10) [5]", "labels": []})
    journal = state_paths.STATE_DIR / "group-decrements.jsonl"
    kw = dict(journal=journal, get_task=td.get, update_task=td.update)
    r1 = dtd_groups.decrement_parent("C", "P", 15, "2026-11-23:split:PH1", **kw)
    r2 = dtd_groups.decrement_parent("C", "P", 10, "2026-11-23:split:PH2", **kw)
    assert (r1["status"], r2["status"]) == ("ok", "ok")      # two splits in a day both count
    assert td.tasks["P"]["content"] == "o [75]"
    uf = _load("undo_fast_groups_split", "undo-fast.py")
    monkeypatch.setattr(uf, "_api", lambda method, path, body=None, timeout=15.0:
                        td.get(path.rsplit("/", 1)[-1]) if method == "GET"
                        else (td.update(path.rsplit("/", 1)[-1], body) if body else None))
    monkeypatch.setattr(uf.mc, "remove_names", lambda names: None)
    errors = []
    uf.reverse_record({"type": "split", "names": ["x"], "task_id": "C", "prev_content": "x (20) [20]",
                       "prev_due": "2026-11-23", "posthoc_id": None, "closed": False,
                       "group": {"key": r1["key"], "parent": "P", "taken": r1["taken"]}}, errors)
    assert errors == [] and td.tasks["P"]["content"] == "o [90]"     # only the first split came back
    sys.modules.pop("state_paths", None)


def test_run_one_off_lowers_the_parent_once_with_an_iso_key(monkeypatch):
    run = _load("run_groups", "run.py")
    child = {"id": "C", "content": "send ticor reply (5) [40]", "labels": ["m5x2"], "parent_id": "P"}
    monkeypatch.setattr(run.todoist, "find_tasks", lambda **k: [child])
    monkeypatch.setattr(run.todoist, "close_task", lambda i: None)
    monkeypatch.setattr(run.excel, "append", lambda *a, **k: None)
    monkeypatch.setattr(run, "_drop_from_queue", lambda i: None)
    monkeypatch.setattr(run, "_append_completed", lambda *a, **k: None)
    monkeypatch.setattr(run, "_fire_refresh", lambda: None)
    monkeypatch.setattr(run.registry, "resolve_fen_col", lambda d: "S")
    import dtd_groups
    paid = []
    monkeypatch.setattr(dtd_groups, "pay_child",
                        lambda c, p, n, day: paid.append((c, p, n, day)) or
                        {"status": "ok", "taken": [[n, None]]})
    assert run.run_one_off("send ticor reply", "11/23", "send ticor reply") == 0
    assert len(paid) == 1 and paid[0][:3] == ("C", "P", 40)
    assert len(paid[0][3]) == 10 and paid[0][3][4] == "-"            # ISO day, not M/D
