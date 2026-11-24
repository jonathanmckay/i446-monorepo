"""lib/dtd_groups.py: one-level task groups (parent + subtasks)."""
import dtd_groups as g

TODAY = "2026-11-23"


def row(i, content, parent=None, due=TODAY, **kw):
    r = {"id": i, "content": content, "due": due, "labels": []}
    if parent:
        r.update(parent_id=parent, parent_due=TODAY, parent_content="outcome [1360]",
                 parent_recurring=False, parent_closed=False)
    r.update(kw)
    return r


def ids(rows):
    return [r["id"] for r in rows]


# --- density ---------------------------------------------------------------

def test_ppm_sums_split_pairs_and_defaults_to_30_minutes():
    assert g.ppm("send reply (5) [40]") == 8
    assert g.ppm("mock (10) [30] @h335 [10] @i9") == 4
    assert g.ppm("no estimate [60]") == 2
    assert g.ppm("no points (10)") == 0


# --- layout ----------------------------------------------------------------

def flags(rows):
    return [(r["id"], "head" if r.get("group_head") else "child" if r.get("group_child") else "")
            for r in rows]


def test_group_is_one_block_outcome_then_inputs_densest_first():
    rows = [row("x", "unrelated (5) [5]"),
            row("a", "funding (10) [30]", "P"),
            row("P", "outcome (240) [1360]"),
            row("y", "another (5) [5]"),
            row("b", "ticor (5) [40]", "P"),
            row("c", "broker (15) [30]", "P")]
    out = g.layout(rows, TODAY)
    # the block sits where its densest input (b) ranked, not where P or a were
    assert flags(out) == [("x", ""), ("y", ""), ("P", "head"), ("b", "child"),
                          ("a", "child"), ("c", "child")]
    assert out[2]["group_size"] == 3
    assert "group_head" not in rows[2] and "group_child" not in rows[1]   # copies


def test_single_input_block():
    out = g.layout([row("P", "outcome [100]"), row("a", "only (5) [10]", "P")], TODAY)
    assert flags(out) == [("P", "head"), ("a", "child")]


def test_children_dropped_when_parent_due_later_closed_or_caller_hid_it():
    later = row("a", "x (5) [10]", "P", parent_due="2026-11-30")
    closed = row("b", "y (5) [10]", "Q", parent_closed=True)
    snoozed = row("c", "z (5) [10]", "R")
    assert g.layout([later, closed, snoozed], TODAY, hidden_parents={"R"}) == []


def test_unknown_or_undated_parent_shows_the_inputs_as_a_headless_block():
    """No stamps at all (parent missing from cache, GET failed): fail open."""
    orphan = {"id": "a", "content": "x (5) [10]", "due": TODAY, "parent_id": "P"}
    assert flags(g.layout([orphan], TODAY)) == [("a", "child")]
    undated = [row("a", "x (5) [10]", "P", parent_due=""), row("z", "plain [1]"),
               row("b", "y (5) [20]", "P", parent_due="")]
    assert flags(g.layout(undated, TODAY)) == [("z", ""), ("b", "child"), ("a", "child")]


def test_parent_without_visible_inputs_stays_a_normal_row():
    rows = [row("P", "outcome [100]"), row("a", "x (5) [10]", "P", parent_closed=True)]
    out = g.layout(rows, TODAY)
    assert flags(out) == [("P", "")]


def test_recurring_parent_is_not_a_group():
    rows = [row("P", "weekly thing [50]", recurring=True),
            row("a", "x (5) [10]", "P", parent_recurring=True),
            row("b", "y (5) [20]", "P", parent_recurring=True)]
    assert flags(g.layout(rows, TODAY)) == [("P", ""), ("a", ""), ("b", "")]


def test_completed_input_is_simply_absent_and_the_rest_keep_their_block():
    """layout() only sees survivors: a just-completed input the caller
    filtered out cannot take its siblings or the outcome with it."""
    survivors = [row("P", "outcome [100]"), row("a", "funding (10) [30]", "P"),
                 row("c", "broker (15) [30]", "P")]
    assert flags(g.layout(survivors, TODAY)) == [("P", "head"), ("a", "child"), ("c", "child")]


def test_agent_inputs_go_last_and_a_block_of_only_agent_inputs_still_shows():
    rows = [row("P", "outcome [100]"),
            row("a", "agent drafts (5) [90]", "P", labels=["agent"]),
            row("b", "send it (5) [20]", "P")]
    assert flags(g.layout(rows, TODAY)) == [("P", "head"), ("b", "child"), ("a", "child")]
    only = [row("P", "outcome [100]"), row("a", "agent drafts (5) [90]", "P", labels=["agent"])]
    assert flags(g.layout(only, TODAY)) == [("P", "head"), ("a", "child")]


def test_pinned_tier_rows_keep_their_place():
    habit = row("h", "0g thing {10}", "P", labels=["#0g"])
    rows = [habit, row("x", "plain [1]"), row("P", "outcome [100]"), row("a", "x (5) [10]", "P")]
    assert flags(g.layout(rows, TODAY)) == [("h", ""), ("x", ""), ("P", "head"), ("a", "child")]


# --- attach_parents --------------------------------------------------------

def test_attach_reads_parent_from_cache_without_fetching():
    cache = {"updated": "x",
             "today": [{"id": "P", "content": "outcome [100]", "due": TODAY, "recurring": False},
                       {"id": "a", "content": "x [10]", "due": TODAY, "parent_id": "P"}]}
    calls = []
    assert g.attach_parents(cache, get_task=lambda i: calls.append(i)) == 1
    a = cache["today"][1]
    assert (a["parent_due"], a["parent_content"], a["parent_closed"]) == (TODAY, "outcome [100]", False)
    assert calls == []


def test_attach_fetches_missing_parent_once_and_marks_closed_or_deleted():
    cache = {"today": [{"id": "a", "content": "x", "parent_id": "P"},
                       {"id": "b", "content": "y", "parent_id": "P"},
                       {"id": "c", "content": "z", "parent_id": "GONE"}]}
    calls = []

    def get(i):
        calls.append(i)
        if i == "GONE":
            return None
        return {"id": "P", "content": "outcome [9]", "checked": False,
                "due": {"date": "2026-12-01", "is_recurring": False}}

    assert g.attach_parents(cache, get_task=get) == 3
    assert calls == ["P", "GONE"]
    assert cache["today"][0]["parent_due"] == "2026-12-01"
    assert cache["today"][2]["parent_closed"] is True


def test_attach_keeps_old_stamps_when_the_fetch_fails():
    cache = {"today": [{"id": "a", "content": "x", "parent_id": "P", "parent_due": TODAY},
                       {"id": "b", "content": "y", "parent_id": "Q"}]}

    def boom(_):
        raise RuntimeError("500")

    assert g.attach_parents(cache, get_task=boom) == 0
    assert cache["today"][0]["parent_due"] == TODAY
    assert "parent_due" not in cache["today"][1]


# --- decrement / restore ---------------------------------------------------

class FakeTodoist:
    def __init__(self, **tasks):
        self.tasks = tasks
        self.updates = []

    def get(self, i):
        t = self.tasks.get(i)
        return dict(t) if t else None

    def update(self, i, body):
        self.updates.append((i, dict(body)))
        self.tasks[i].update(body)


def test_decrement_takes_paid_points_and_labels_the_parent(tmp_path):
    td = FakeTodoist(P={"content": "purchase l912 (240) [1360]", "labels": ["m5x2"]})
    j = tmp_path / "j.jsonl"
    rec = g.decrement_parent("a", "P", 40, TODAY, journal=j, get_task=td.get, update_task=td.update)
    assert rec["status"] == "ok" and rec["taken"] == [[40, None]]
    assert td.tasks["P"]["content"] == "purchase l912 (240) [1320]"
    assert td.tasks["P"]["labels"] == ["m5x2", g.GROUP_LABEL]


def test_decrement_is_idempotent_per_child_and_day(tmp_path):
    td = FakeTodoist(P={"content": "o [100]", "labels": [g.GROUP_LABEL]})
    j = tmp_path / "j.jsonl"
    kw = dict(journal=j, get_task=td.get, update_task=td.update)
    assert g.decrement_parent("a", "P", 30, TODAY, **kw)["status"] == "ok"
    assert g.decrement_parent("a", "P", 30, TODAY, **kw)["status"] == "dup"
    assert td.tasks["P"]["content"] == "o [70]"
    assert g.decrement_parent("b", "P", 30, TODAY, **kw)["status"] == "ok"
    assert td.tasks["P"]["content"] == "o [40]"


def test_decrement_floors_at_zero_and_records_what_was_actually_taken(tmp_path):
    td = FakeTodoist(P={"content": "o [25]", "labels": [g.GROUP_LABEL]})
    rec = g.decrement_parent("a", "P", 40, TODAY, journal=tmp_path / "j",
                             get_task=td.get, update_task=td.update)
    assert rec["taken"] == [[25, None]] and td.tasks["P"]["content"] == "o [0]"


def test_decrement_skips_recurring_closed_and_unpaid(tmp_path):
    td = FakeTodoist(R={"content": "o [50]", "labels": [], "due": {"is_recurring": True}},
                     C={"content": "o [50]", "labels": [], "checked": True})
    kw = dict(journal=tmp_path / "j", get_task=td.get, update_task=td.update)
    assert g.decrement_parent("a", "R", 10, TODAY, **kw)["status"] == "skip"
    assert g.decrement_parent("a", "C", 10, TODAY, **kw)["status"] == "skip"
    assert g.decrement_parent("a", "R", 0, TODAY, **kw)["status"] == "skip"
    assert td.updates == []


def test_decrement_error_is_not_journaled_so_a_retry_can_land(tmp_path):
    td = FakeTodoist(P={"content": "o [100]", "labels": [g.GROUP_LABEL]})
    j = tmp_path / "j"

    def boom(_i, _b):
        raise RuntimeError("500")

    assert g.decrement_parent("a", "P", 10, TODAY, journal=j, get_task=td.get,
                              update_task=boom)["status"] == "error"
    assert g.decrement_parent("a", "P", 10, TODAY, journal=j, get_task=td.get,
                              update_task=td.update)["status"] == "ok"
    assert td.tasks["P"]["content"] == "o [90]"


def test_restore_adds_back_only_its_own_share(tmp_path):
    """Undo of child a must not wipe sibling b's later decrement."""
    td = FakeTodoist(P={"content": "o [100]", "labels": [g.GROUP_LABEL]})
    kw = dict(journal=tmp_path / "j", get_task=td.get, update_task=td.update)
    g.decrement_parent("a", "P", 30, TODAY, **kw)
    g.decrement_parent("b", "P", 20, TODAY, **kw)
    assert g.restore_parent("a", TODAY, **kw)["status"] == "ok"
    assert td.tasks["P"]["content"] == "o [80]"
    assert g.restore_parent("a", TODAY, **kw)["status"] == "none"      # only once
    assert g.decrement_parent("a", "P", 30, TODAY, **kw)["status"] == "ok"   # redo works
    assert td.tasks["P"]["content"] == "o [50]"


def test_restore_after_floor_returns_what_was_taken_not_what_was_paid(tmp_path):
    td = FakeTodoist(P={"content": "o [25]", "labels": [g.GROUP_LABEL]})
    kw = dict(journal=tmp_path / "j", get_task=td.get, update_task=td.update)
    g.decrement_parent("a", "P", 40, TODAY, **kw)
    g.restore_parent("a", TODAY, **kw)
    assert td.tasks["P"]["content"] == "o [25]"


def test_split_pairs_keep_their_codes_through_take_and_add_back(tmp_path):
    td = FakeTodoist(P={"content": "o [30] @h335 [10] @i9", "labels": [g.GROUP_LABEL]})
    kw = dict(journal=tmp_path / "j", get_task=td.get, update_task=td.update)
    rec = g.decrement_parent("a", "P", 35, TODAY, **kw)
    assert rec["taken"] == [[30, "h335"], [5, "i9"]]
    assert td.tasks["P"]["content"] == "o [0] @h335 [5] @i9"
    g.restore_parent("a", TODAY, **kw)
    assert td.tasks["P"]["content"] == "o [30] @h335 [10] @i9"


# --- parents ----------------------------------------------------------------

def test_group_parent_detection():
    cache = {"today": [row("P", "outcome [100]"), row("a", "x [10]", "P"),
                       row("L", "labelled [5]", labels=["grp"]),
                       row("R", "weekly [5]", labels=["grp"], recurring=True),
                       row("x", "plain [5]")]}
    ps = g.parent_ids(cache)
    assert ps == {"P"}
    assert [r["id"] for r in cache["today"] if g.is_group_parent(r, ps)] == ["P", "L"]
