"""User request 2026-10-29: math, english and 中文 (teaching sessions with the
kids) are worth 1分/min plus a fixed +5 per session, credited to xk87 (0分!X).
"math" used to alias to the retired 问学 0n habit, and a bare "math" would
otherwise name-match "goodnight math & lights out [15]" in Todoist."""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("didfast_tsb", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_spec)
sys.modules["didfast_tsb"] = df
_spec.loader.exec_module(df)

TQ = {"0neon": [{"id": "1", "content": "goodnight math & lights out (5) [15]",
                 "labels": ["xk87"]}]}


def _route(text, tq=None):
    (r,) = df.route_items(df.parse_input(text), {"0n": {}, "1n": {}}, tq or {})
    return r


@pytest.mark.parametrize("name", ["math", "english", "中文"])
def test_typed_minutes_earn_one_per_minute_plus_five(name):
    r = _route(f"{name} 30", TQ)
    assert (r.step, r.fen_col, r.fen_points) == ("variable", "X", 35)
    assert r.todoist_task is None


def test_math_is_no_longer_the_wenxue_habit():
    (it,) = df.parse_input("math 20")
    assert it.name == "math"


def test_timer_minutes_get_the_session_bonus():
    r = _route("english", TQ)
    df.apply_timer_minutes([r], {"description": "english", "minutes": 25})
    assert r.fen_points == 30


def test_explicit_points_override_the_formula():
    r = _route("中文 [12]", TQ)
    assert r.fen_points == 12


def test_dtd_selected_task_still_matches_by_id():
    r = df.route_items(df.parse_input("math"), {"0n": {}, "1n": {}}, TQ,
                       preferred_id="1")[0]
    assert r.step == "todoist" and r.fen_points == 15
