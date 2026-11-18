"""Regression test: /fin on a weekly card credited it as a plain task.

Bug 2026-11-15 (JM 2026-11-17: "I thought I set 1g, but I don't see it here in
1neon"): done_session.py closes a claimed card with
`did-fast.py --task-id <id> "1g (20) [20]"`. parse_input leaves the "(20)"
estimate in the name, "1g (20)" matched no 1n+ header, and the card went down
the plain-task path: +20 to 0分, Todoist closed, 1n+ never marked.
"""
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("didfast_cardtext", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_spec)
sys.modules["didfast_cardtext"] = df
_spec.loader.exec_module(df)

HEADERS = {"0n": {"0l": 20}, "1n": {"1g": "D", "1 xk87 wknd": "Q"}}
TQ = {"0neon": [], "夜neon": [], "1neon": []}


def _route(inp):
    return df.route_items(df.parse_input(inp), HEADERS, TQ, skip_todoist=True)


def test_weekly_card_text_routes_to_1n():
    (r,) = _route("1g (20) [20]")
    assert r.step == "1n" and r.item.name == "1g"


def test_multiword_weekly_card_text_routes_to_1n():
    (r,) = _route("1 xk87 wknd (20) [20]")
    assert r.step == "1n" and r.item.name == "1 xk87 wknd"


def test_daily_card_text_routes_to_0n():
    (r,) = _route("0l (8) [20]")
    assert r.step == "0n" and r.col_num == 20


def test_non_habit_task_keeps_its_estimate():
    (r,) = _route("call the dentist (10) [5]")
    assert r.step not in ("0n", "1n") and r.item.name == "call the dentist (10)"
