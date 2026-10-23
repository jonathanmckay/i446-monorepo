#!/usr/bin/env python3
"""Regression (2026-10-23): "I split a 0g with {54} that should have been
recorded into neon, but it wasn't". dtd's ctrl-p split credits a goal's done
bonus as `did-fast --points-only "<name> {N}"`. A name with no domain (0g
goals like "1500分") routed to needs_agent ("no match, needs domain
disambiguation"), so the {N} never reached 0分!Q. A bonus-only item must
route without a domain and let the curly append credit Q."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("did_fast_bonus", Path(__file__).parent / "did-fast.py")
df = importlib.util.module_from_spec(_spec)
sys.modules["did_fast_bonus"] = df
_spec.loader.exec_module(df)


def _route(raw):
    items = df.parse_input(raw)
    return df.route_items(items, {}, {}, skip_todoist=True)


def test_bonus_only_goal_routes_to_fast_path_with_curly_points():
    (r,) = _route("1500分 {56}")
    assert r.step == "variable"          # in did-fast's `fast` set, not needs_agent
    assert r.fen_col is None             # no domain column credited
    assert r.item.curly_points == 56     # → the {N} append writes 0分!Q


def test_square_points_without_domain_still_needs_agent():
    (r,) = _route("1500分 [56]")
    assert r.step == "needs_agent"
