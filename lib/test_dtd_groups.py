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


# --- a live habit adopted by its dated copies (display only) -----------------

def _backlog():
    return [row("h", "ibx m5x2 (4) [15]", labels=["0neon", "m5x2"], recurring=True),
            row("x", "unrelated (5) [5]"),
            row("P", "clear m5x2 email backlog (100) {60}", labels=["grp"]),
            row("d16", "ibx m5x2 11.16 (30) [15]", "P"),
            row("d03", "ibx m5x2 11.03 (2) [15]", "P")]


def test_live_habit_leads_the_block_of_its_dated_copies_at_its_own_place():
    rows = _backlog()
    out = g.layout(rows, TODAY)
    assert flags(out) == [("P", "head"), ("h", "child"), ("d03", "child"),
                          ("d16", "child"), ("x", "")]
    assert out[1]["group_adopted"] and not out[1].get("parent_id")   # still the habit
    assert "group_adopted" not in out[2] and "group_child" not in rows[0]


def test_no_adoption_without_a_visible_dated_copy_or_for_a_one_off_namesake():
    rows = _backlog()[:3]                                   # day cards done or hidden
    assert flags(g.layout(rows, TODAY)) == [("h", ""), ("x", ""), ("P", "")]
    rows = _backlog()
    rows[0]["recurring"] = False                            # a one-off that shares the name
    assert ("h", "") in flags(g.layout(rows, TODAY))
    rows = _backlog()
    rows[0]["content"] = "ibx i9 (4) [20]"                  # another habit
    assert flags(g.layout(rows, TODAY))[0] == ("h", "")


def test_a_habit_is_adopted_by_one_group_only():
    rows = _backlog() + [row("Q", "second outcome [50]"), row("q1", "ibx m5x2 11.20 (5) [15]", "Q")]
    out = g.layout(rows, TODAY)
    assert [i for i, f in flags(out) if i == "h"] == ["h"]
    assert ids(out).index("h") == ids(out).index("P") + 1   # first group in display order


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


# --- cache overlay: the parent's new [N] shows without a refresh ------------

import json
import os


def _cache(tmp_path, *rows):
    p = tmp_path / g.CACHE_NAME
    p.write_text(json.dumps({"updated": "2026-11-23T20:32:41", "today": list(rows)}))
    return p


def _today(p):
    return {r["id"]: r for r in json.loads(p.read_text())["today"]}


def test_paying_an_input_patches_the_cached_parent_and_its_short_name(tmp_path):
    """JM 2026-11-23: closed "improve password manager situation [10]" and dtd
    kept showing the parent at [120] until a manual refresh. Both surfaces
    render `short`, which carries its own copy of the estimates."""
    p = _cache(tmp_path,
               row("P", "migrate to 1password and enable agentic password management (70) [120]",
                   short="Migrate to 1Password and enable (70) [120]", labels=[g.GROUP_LABEL]),
               row("c", "improve password manager situation [10] (30)", "P",
                   parent_content="migrate to 1password and enable agentic password management (70) [120]"),
               row("x", "unrelated (5) [120]", short="unrelated (5) [120]"))
    td = FakeTodoist(P={"content": "migrate to 1password and enable agentic password management (70) [120]",
                        "labels": [g.GROUP_LABEL]})
    rec = g.decrement_parent("c", "P", 10, TODAY, journal=tmp_path / "group-decrements.jsonl",
                             get_task=td.get, update_task=td.update)
    assert rec["status"] == "ok"
    rows = _today(p)
    assert rows["P"]["content"].endswith("(70) [110]")
    assert rows["P"]["short"] == "Migrate to 1Password and enable (70) [110]"
    assert rows["c"]["parent_content"].endswith("(70) [110]")
    assert rows["x"]["content"] == "unrelated (5) [120]"            # same number, other task
    assert json.loads(p.read_text())["updated"] == "2026-11-23T20:32:41"


def test_patch_moves_the_mtime_to_a_new_second_so_dtd_sh_reloads(tmp_path):
    p = _cache(tmp_path, row("P", "o [100]"))
    before = int(p.stat().st_mtime)
    td = FakeTodoist(P={"content": "o [100]", "labels": []})
    g.decrement_parent("c", "P", 10, TODAY, journal=tmp_path / "j", get_task=td.get, update_task=td.update)
    assert int(p.stat().st_mtime) > before
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_undo_patches_the_cache_back_and_a_sibling_decrement_survives(tmp_path):
    p = _cache(tmp_path, row("P", "o [100]", short="o [100]"))
    td = FakeTodoist(P={"content": "o [100]", "labels": []})
    kw = dict(journal=tmp_path / "j", get_task=td.get, update_task=td.update)
    g.decrement_parent("a", "P", 30, TODAY, **kw)
    g.decrement_parent("b", "P", 20, TODAY, **kw)
    assert _today(p)["P"]["content"] == "o [50]"
    g.restore_parent("a", TODAY, **kw)
    assert _today(p)["P"]["content"] == "o [80]" == td.tasks["P"]["content"]
    assert _today(p)["P"]["short"] == "o [80]"


def test_a_refresh_that_fetched_before_the_decrement_is_corrected_on_its_way_out(tmp_path):
    """The refreshers and the launchd writer copy rows forward or fetch lagged
    filter results; attach_parents lays the journal over whatever they hold."""
    j = tmp_path / "j"
    td = FakeTodoist(P={"content": "o (5) [100]", "labels": []})
    g.decrement_parent("a", "P", 30, TODAY, journal=j, get_task=td.get, update_task=td.update)
    stale = {"today": [row("P", "o (5) [100]", short="o (5) [100]"), row("a", "in [30]", "P")]}
    g.attach_parents(stale, journal=j)
    assert stale["today"][0]["content"] == "o (5) [70]"
    assert stale["today"][0]["short"] == "o (5) [70]"
    assert stale["today"][1]["parent_content"] == "o (5) [70]"


def test_overlay_leaves_fresh_edited_and_old_rows_alone(tmp_path):
    j = tmp_path / "j"
    td = FakeTodoist(P={"content": "o [100]", "labels": []})
    kw = dict(journal=j, get_task=td.get, update_task=td.update)
    g.decrement_parent("a", "P", 30, TODAY, **kw)                    # 100 -> 70
    g.decrement_parent("b", "P", 20, TODAY, **kw)                    # 70 -> 50
    g.restore_parent("a", TODAY, **kw)                               # 50 -> 80
    g.decrement_parent("d", "P", 10, TODAY, **kw)                    # 80 -> 70 (70 seen twice)

    def after(content, **kw2):
        c = {"today": [row("P", content)]}
        g.overlay_recent(c, j, **kw2)
        return c["today"][0]["content"]

    assert after("o [70]") == "o [70]"          # already newest: not sent round again
    assert after("o [100]") == "o [70]"         # oldest: walked the whole chain
    assert after("o [50]") == "o [70]"
    assert after("o [999]") == "o [999]"        # JM edited it: not ours to touch
    assert after("o [100]", now=__import__("time").time() + g.OVERLAY_MAX_AGE + 5) == "o [100]"


def test_short_that_does_not_carry_the_estimates_is_dropped_not_guessed(tmp_path):
    j = tmp_path / "j"
    td = FakeTodoist(P={"content": "o (5) [100]", "labels": []})
    g.decrement_parent("a", "P", 30, TODAY, journal=j, get_task=td.get, update_task=td.update)
    c = {"today": [row("P", "o (5) [100]", short="o")]}
    g.overlay_recent(c, j)
    assert c["today"][0]["content"] == "o (5) [70]" and "short" not in c["today"][0]


def test_no_cache_beside_the_journal_is_a_quiet_no_op(tmp_path):
    td = FakeTodoist(P={"content": "o [100]", "labels": []})
    rec = g.decrement_parent("a", "P", 30, TODAY, journal=tmp_path / "j",
                             get_task=td.get, update_task=td.update)
    assert rec["status"] == "ok" and not (tmp_path / g.CACHE_NAME).exists()
    assert not (tmp_path / "task-queue.lock").exists()
