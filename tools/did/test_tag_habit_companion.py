#!/usr/bin/env python3
"""Feature (2026-11-19, JM): "make it so the tag is worth additional points
when the did command is run (rather than needing a separate call)".

Tue 11/17 he logged `1955-2145 strategy deck @i9 [210]`: the deck's own 100
plus 110 minutes of 业写, typed as one number, so the 业写 week cell stayed
empty. A #业写 tag (TAG_1N_HABITS) now makes did-fast add the habit itself:
the tagged item keeps its [N], and a companion 1n+ result credits 业写 for
the item's minutes exactly as a typed "业写 110" would.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("df_tagc", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["df_tagc"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def df():
    return _load()


HEADERS = {"0n": {}, "1n": {"业写": "AB", "1 cal": "R"}}
CARD = {"id": "6h8G3HQHGGMjFgFc", "content": "业写 (60) [30]",
        "labels": ["1neon", "i9"]}
TQ = {"0neon": [], "夜neon": [], "1neon": [CARD], "today": []}


def _run(df, text, tq=TQ, **kw):
    items = df.parse_input(text)
    df.infer_range_blocks(items)
    routes = df.route_items(items, HEADERS, tq, skip_todoist=True)
    comps = df.tag_habit_companions(routes, HEADERS, tq, **kw)
    return routes, comps


def test_tagged_range_task_keeps_its_points_and_earns_the_habit(df):
    routes, comps = _run(df, "strategy deck 1955-2145 @i9 #业写 [100]")
    (parent,) = routes
    assert parent.step == "variable" and parent.fen_points == 100
    assert parent.item.toggl_tags == ["业写"]      # the Toggl entry stays tagged
    (c,) = comps
    assert c.step == "1n" and c.is_variable_1n
    assert c.col_letter == "AB"
    assert c.write_value == 110                    # minutes in the week cell
    assert c.variable_value == 147                 # 4/3 a minute
    assert c.fen_col == "R"                        # 业写 pays i9
    assert c.item.tag_companion_of == "strategy deck"


def test_typed_minutes_work_without_a_range(df):
    _, comps = _run(df, "roadmap doc 45 @i9 #业写")
    (c,) = comps
    assert c.write_value == 45 and c.variable_value == 60


def test_untagged_and_meta_tagged_items_earn_nothing_extra(df):
    assert _run(df, "strategy deck 1955-2145 @i9 [100]")[1] == []
    assert _run(df, "strategy deck 1955-2145 @i9 #-2 [100]")[1] == []


def test_no_minutes_means_no_companion_and_a_warning(df, capsys):
    """Timer path: the length arrives later from the Toggl stop, matched by
    the PARENT's name. Crediting a guess would be worse than a warning."""
    _, comps = _run(df, "strategy deck @i9 #业写 [100]")
    assert comps == []
    assert "NOT credited" in capsys.readouterr().err


def test_janus_habit_tag_extra_is_not_doubled(df):
    """mobile.py log_entry already appends ', <tag> <minutes>' for a habit
    tag; the batch then holds 业写 itself and the companion must stand down."""
    routes, comps = _run(df, "strategy deck 1955-2145 @i9 #业写 [100], 业写 110")
    assert [r.item.name for r in routes if r.step == "1n"] == ["业写"]
    assert comps == []


def test_habit_tagged_with_itself_is_not_doubled(df):
    routes, comps = _run(df, "业写 60 #业写")
    assert len(routes) == 1 and comps == []


def test_parent_that_did_not_land_earns_no_habit(df):
    """No @domain → needs_agent. Its tag must not credit an orphan."""
    items = df.parse_input("mystery thing 1955-2145 #业写")
    routes = df.route_items(items, HEADERS, TQ, skip_todoist=True)
    assert routes[0].step == "needs_agent"
    assert df.tag_habit_companions(routes, HEADERS, TQ) == []


def test_companion_never_makes_a_toggl_entry_or_stops_a_timer(df):
    """It shares the parent's range; 6b's toggl_items and 3b's all_names key
    on tag_companion_of to leave Toggl alone. Guard the source of both."""
    src = (_HERE / "did-fast.py").read_text()
    assert "if r.item.time_range and not r.item.tag_companion_of]" in src
    assert "[r.item.name for r in fast if not r.item.tag_companion_of]" in src


def test_two_tagged_sessions_in_a_day_are_two_credits(df):
    """They share the weekly card, hence the task id; the idem key must
    differ per session or the daemon drops the second as a replay."""
    _, a = _run(df, "deck 0900-1000 @i9 #业写 [50]")
    _, b = _run(df, "memo 1400-1430 @i9 #业写 [20]")
    a[0].todoist_task = b[0].todoist_task = CARD
    ka = df._idem_key("0fen", a[0], "11/19", "R")
    kb = df._idem_key("0fen", b[0], "11/19", "R")
    assert ka and kb and ka != kb
    # a retry of the SAME completion still dedupes
    assert ka == df._idem_key("0fen", a[0], "11/19", "R")


def test_split_per_block_keeps_minutes_and_points_whole(df):
    """147 分 over 戌/亥 splits like any per-minute result: segment minutes
    append to the same week cell, segment points to R, nothing doubled."""
    _, comps = _run(df, "strategy deck 1955-2145 @i9 #业写 [100]")
    parts = df.split_results_by_block(comps)
    assert sum(p.write_value for p in parts) == 110
    assert len(parts) == 2
    assert sum(p.variable_value for p in parts) == 147
    assert all(p.item.tag_companion_of for p in parts)


# ── #relax (2026-11-23): 1分/min to the weekly relax habit, 个 column ────────
RELAX_HEADERS = {"0n": {}, "1n": {"relax {60}": "AH", "业写": "AB"}}
RELAX_CARD = {"id": "6h8rcv4JgH2qP99x", "content": "relax (60) [15]",
              "labels": ["1neon", "hcm"]}
RELAX_TQ = {"0neon": [], "夜neon": [], "1neon": [RELAX_CARD], "today": []}


def _run_relax(df, text):
    items = df.parse_input(text)
    df.infer_range_blocks(items)
    routes = df.route_items(items, RELAX_HEADERS, RELAX_TQ, skip_todoist=True)
    return routes, df.tag_habit_companions(routes, RELAX_HEADERS, RELAX_TQ)


def test_relax_tag_credits_minutes_to_the_weekly_habit(df):
    routes, comps = _run_relax(df, "movie with LX 2000-2130 @xk88 #relax [30]")
    assert routes[0].item.points_override == 30    # the task keeps its own [30]
    assert routes[0].item.toggl_tags == ["relax"]
    assert len(comps) == 1
    c = comps[0]
    assert c.step == "1n"
    assert c.write_value == 90                     # minutes to the week cell
    assert c.variable_value == 90                  # 1分/min
    assert c.fen_col == "T"                        # relax pays 个
    assert c.item.tag_companion_of


def test_relax_tag_stands_down_when_janus_already_added_it(df):
    """janus appends ", relax <minutes>" for a habit tag (mobile.py habit_tags);
    the batch then resolves to header "relax {60}" and the tag must not add a
    second credit. Compared alias-resolved, or this doubles."""
    routes, comps = _run_relax(df, "movie 2000-2130 @xk88 #relax [30], relax 90")
    assert [r.item.name for r in routes if r.step == "1n"] == ["relax"]
    assert comps == []


def test_relax_self_tag_is_one_credit(df):
    routes, comps = _run_relax(df, "relax 60 #relax")
    assert [r.step for r in routes] == ["1n"]
    assert comps == []


# ── 一起饭 under an outside project (2026-11-24, JM) ─────────────────────────────
# "一起饭 if it's in the s897 or 家 projects but has the xk87 tag gets the points
# it would for a family dinner together and also the 家 or family points."

MEAL_HEADERS = {"0n": {}, "1n": {"一起饭": "AC", "family": "AF", "s897": "AG"}}
MEAL_TQ = {"0neon": [], "夜neon": [], "1neon": [], "today": []}


def _meal(df, text):
    items = df.parse_input(text)
    df.infer_range_blocks(items)
    routes = df.route_items(items, MEAL_HEADERS, MEAL_TQ, skip_todoist=True)
    return routes, df.tag_habit_companions(routes, MEAL_HEADERS, MEAL_TQ, skip_todoist=True)


@pytest.mark.parametrize("text,habit,col,fen", [
    ("一起饭 1800-1900 @家 #xk87", "family", "AF", "X"),     # janus's shape for a Toggl entry
    ("一起饭 60 @s897 #xk87", "s897", "AG", "Y"),
    ("一起吃 60 @家 #xk87", "family", "AF", "X"),            # the alias earns it too
])
def test_family_meal_in_an_outside_project_earns_both(df, text, habit, col, fen):
    routes, comps = _meal(df, text)
    (meal,) = routes
    assert meal.step == "1n" and meal.fen_col == "X"
    assert meal.write_value == 15 and meal.variable_value == 75   # 一起饭 itself is unchanged
    (c,) = comps
    assert c.item.name == habit and c.step == "1n" and c.col_letter == col
    assert c.write_value == 60 and c.variable_value == 60        # the project habit: 1/m
    assert c.fen_col == fen
    assert c.item.tag_companion_of                                # never its own Toggl entry


@pytest.mark.parametrize("text", [
    "一起饭 60 @家",              # no xk87 tag: not a family dinner
    "一起饭 60 @xk87 #xk87",      # the home project: 一起饭 alone
    "一起饭 60 #xk87",
    "family 60 @家 #xk87",        # only 一起饭 carries the rule
])
def test_meal_without_the_tag_or_the_outside_project_earns_only_itself(df, text):
    routes, comps = _meal(df, text)
    assert len(routes) == 1 and comps == []


def test_meal_companion_is_not_doubled_when_the_habit_is_typed_too(df):
    routes, comps = _meal(df, "一起饭 60 @家 #xk87, family 60")
    assert len(routes) == 2 and comps == []
