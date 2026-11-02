#!/usr/bin/env python3
"""edit-fast + a multi-domain split task ("[N] @code [N] @code",
lib/fen_split.py): the form prefills the total and a clean name, a rename
keeps the pairs, a retyped total collapses them, retyped pairs replace them.

Run: python3 -m pytest tools/did/test_edit_fast_fen_split.py -v
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load(name, fn):
    spec = importlib.util.spec_from_file_location(name, HERE / fn)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ef = _load("edit_fast_split_under_test", "edit-fast.py")
pf = _load("points_fast_split_under_test", "points-fast.py")

SPLIT = "mock WAU tree (30) [30] @i9 [10] @hcb"


def test_current_fields_total_and_clean_name():
    cur = ef.current_fields({"content": SPLIT, "labels": ["i9"]}, "")
    assert cur["name"] == "mock WAU tree"
    assert cur["points"] == "40"
    assert cur["domain"] == "i9"


def test_set_name_keeps_the_pairs():
    assert ef.set_name(SPLIT, "mock WAU debugging tree") == "mock WAU debugging tree (30) [30] @i9 [10] @hcb"


def test_set_points_collapses_the_split():
    assert pf.set_points(SPLIT, 25) == "mock WAU tree (30) [25]"
    assert pf.set_points("fix faucet (30) [8]", 12) == "fix faucet (30) [12]"


def test_parse_line_pairs():
    out = ef.parse_line("mock WAU tree [20] @h335 [5] @i9 [5]")
    assert out["pairs"] == "[20] @h335 [5] @i9 [5]"
    assert out["points"] == "30"
    assert out["domain"] == "h335"
    assert out["name"] == "mock WAU tree"


def test_parse_line_single_unchanged():
    out = ef.parse_line("fix faucet @家 [8]")
    assert out == {"domain": "家", "points": "8", "name": "fix faucet"}
