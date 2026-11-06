#!/usr/bin/env python3
"""Regression (JM 2026-11-05): "@xk20/@xk22/@xk26" is a kid route — the
minutes go to that kid's 0n column even when the item fuzzy-matches an
xk87-labelled Todoist card, whose own label would otherwise send its [N] to
0分!X. xk26 (Rori) has the route too.

Run: python3 -m pytest tools/did/test_did_fast_kid_route.py -v
"""
import importlib.util
import sys
from pathlib import Path

DID_FAST = Path(__file__).parent / "did-fast.py"


def _load_did_fast():
    spec = importlib.util.spec_from_file_location("did_fast_kid_under_test", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


df = _load_did_fast()
CARD = {"content": "playground with the kids (30) [20]", "labels": ["xk87"]}


def test_xk26_has_a_kid_route():
    assert df.LABEL_TO_0FEN["xk26"] == "0n:xk26"


def test_kid_override_beats_card_label():
    (it,) = df.parse_input("playground with the kids 1500-1530 @xk20")
    pts, col, extra = df._task_credit(it, CARD)
    assert col == "0n:xk20" and extra == [] and pts == 20


def test_no_override_keeps_card_label():
    (it,) = df.parse_input("playground with the kids 1500-1530")
    _, col, _ = df._task_credit(it, CARD)
    assert col == df._project_fen_col(["xk87"])
