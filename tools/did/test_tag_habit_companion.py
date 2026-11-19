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
