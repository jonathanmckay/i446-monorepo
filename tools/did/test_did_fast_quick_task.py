#!/usr/bin/env python3
"""Quick task from the janus text box (JM 2026-11-01):

    +10 Neon off ledger (5)

= "I did this for the last 5 minutes, it is worth 10分". Not a 分 log: the
item gets a Toggl range now-5..now, a posthoc record and the points, which go
to 个 unless an @code is typed. A plain "+10 @i9 note" (no trailing (M)) is
still the ledger-only 分 log.

Run: python3 -m pytest tools/did/test_did_fast_quick_task.py -v
"""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

DID_FAST = Path(__file__).parent / "did-fast.py"


def _load_did_fast():
    spec = importlib.util.spec_from_file_location("did_fast_quick_under_test", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


df = _load_did_fast()


def _minutes(tr):
    s, e = (datetime.strptime(t, "%H%M") for t in tr)
    return int((e - s).total_seconds() // 60)


def test_quick_task_parses_minutes_points_and_name():
    (it,) = df.parse_input("+10 Neon off ledger (5)")
    assert it.quick_task and not it.points_log
    assert it.name == "Neon off ledger"
    assert it.points_override == 10
    assert it.time_range is not None and _minutes(it.time_range) == 5
    now = df._daytime.local_now()
    end = datetime.strptime(it.time_range[1], "%H%M")
    assert abs((now.hour * 60 + now.minute) - (end.hour * 60 + end.minute)) <= 1


def test_quick_task_keeps_code():
    (it,) = df.parse_input("+10 @i9 read COD deck (12)")
    assert it.quick_task and it.project_override == "i9"
    assert it.name == "read COD deck" and _minutes(it.time_range) == 12


def test_plain_points_log_unchanged():
    (it,) = df.parse_input("+90 @m5x2 monthly sync")
    assert it.points_log and not it.quick_task and it.time_range is None
    (neg,) = df.parse_input("-15 @m5x2 late fee (5)")
    assert neg.points_log and not neg.quick_task  # a negative "task" is a log


def test_quick_task_routes_to_self_without_code():
    (it,) = df.parse_input("+10 Neon off ledger (5)")
    (r,) = df.route_items([it], headers={}, tq={}, skip_todoist=True)
    assert r.step == "variable"
    assert r.fen_points == 10
    assert r.fen_col == df.neon_cols.domain_col("0分", "个")
    assert r.item.time_range is not None


def test_quick_task_routes_to_code():
    (it,) = df.parse_input("+10 @i9 read COD deck (12)")
    (r,) = df.route_items([it], headers={}, tq={}, skip_todoist=True)
    assert r.step == "variable" and r.fen_col == df.LABEL_TO_0FEN["i9"] and r.fen_points == 10
