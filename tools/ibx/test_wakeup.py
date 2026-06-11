#!/usr/bin/env python3
"""Unit tests for wakeup.py pure response parsers (no TUI / no side effects)."""

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_spec = importlib.util.spec_from_file_location("wakeup", _HERE / "wakeup.py")
wakeup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wakeup)


def _parse_goals_text(text):
    """Minimal stand-in matching -2n.parse_goals_text semantics for the
    comma/newline split (kept local so the test has no Excel/network deps)."""
    items = []
    for line in (text or "").splitlines():
        items.extend(p.strip() for p in line.split(","))
    return [i for i in items if i]


SUGG = [
    {"content": "ship pnl deck"},
    {"content": "review NOI flags"},
    {"content": "call broker"},
]


def test_goal_picks_single():
    assert wakeup.parse_goal_response("2", SUGG, _parse_goals_text) == \
        ["review NOI flags"]


def test_goal_picks_multiple():
    assert wakeup.parse_goal_response("1,3", SUGG, _parse_goals_text) == \
        ["ship pnl deck", "call broker"]


def test_goal_picks_out_of_range_rejected():
    # "9" is not a valid pick and there are no other picks → invalid
    assert wakeup.parse_goal_response("9", SUGG, _parse_goals_text) is None


def test_goal_freetext():
    assert wakeup.parse_goal_response("write memo, send email", SUGG,
                                      _parse_goals_text) == \
        ["write memo", "send email"]


def test_goal_skip_and_empty_rejected():
    assert wakeup.parse_goal_response("skip", SUGG, _parse_goals_text) is None
    assert wakeup.parse_goal_response("", SUGG, _parse_goals_text) is None
    assert wakeup.parse_goal_response("   ", SUGG, _parse_goals_text) is None


HABITS = [{"habit": "drink water"}, {"habit": "make bed"}]


def test_task_pick_number():
    assert wakeup.resolve_task_response("2", HABITS) == "make bed"


def test_task_freetext():
    assert wakeup.resolve_task_response("stretch", HABITS) == "stretch"


def test_task_number_out_of_range_is_freetext():
    # A number with no matching habit is treated as free text (still valid).
    assert wakeup.resolve_task_response("9", HABITS) == "9"


def test_task_skip_empty_rejected():
    assert wakeup.resolve_task_response("skip", HABITS) is None
    assert wakeup.resolve_task_response("", HABITS) is None
    assert wakeup.resolve_task_response(None, HABITS) is None


def test_timer_default_on_empty():
    assert wakeup.resolve_timer_desc("", "ship pnl deck") == "ship pnl deck"


def test_timer_override():
    assert wakeup.resolve_timer_desc("call broker", "ship pnl deck") == \
        "call broker"


def test_timer_skip_rejected():
    assert wakeup.resolve_timer_desc("skip", "ship pnl deck") is None


def test_timer_empty_no_default_rejected():
    assert wakeup.resolve_timer_desc("", "") is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
