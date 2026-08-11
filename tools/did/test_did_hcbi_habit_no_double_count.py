"""Regression test (2026-08-10): "did bball" wrote its points to BOTH hcbi!Y224
(via HCBI_HABITS' minutes append) AND directly onto 0分!W (via the generic
fen_appends points append) — but 0分!W is a FORMULA
(``=hcbi!AA224+hcbi!Y224+...``), not a plain accumulator, so hcbi!Y224's own
write already flows into it. The direct append double-counted the same
session (confirmed live: 0分!W held a raw "+52" on top of a formula that
already summed hcbi!Y224, which itself had just received "+52" from the same
"did bball" call) until manually stripped from the sheet.

Fix: skip an item's generic 0分 points append when its name is also a
HCBI_HABITS key — its contribution already reaches 0分 through the hcbi sheet
write. {N} curly points (0g bonus, column Q) are a separate mechanism and
must NOT be caught by the same guard.
"""
from __future__ import annotations

import ast
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "did-fast.py").read_text()
TREE = ast.parse(SRC)


def _main_body() -> str:
    fn = next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and n.name == "main")
    return ast.get_source_segment(SRC, fn)


def test_bball_is_registered_in_both_maps():
    # The bug only reproduces when a habit is a member of BOTH maps at once —
    # confirm the fixture the bug report was about still exists in both.
    assert '"bball": ("hcbp", "W")' in SRC
    assert '"bball": "Y"' in SRC


def test_fen_appends_skips_hcbi_habit_items():
    body = _main_body()
    # The loop that builds the batched 0分 appends must exclude anything
    # already routed through HCBI_HABITS, or its points/minutes get counted
    # twice (once raw on 0分, once via the hcbi-referencing formula).
    start = body.index("fen_appends = []")
    fen_loop = body[start:body.index("fen_result = None", start)]
    assert "HCBI_HABITS" in fen_loop, \
        "fen_appends loop must check HCBI_HABITS to avoid double-counting hcbi-routed habits"
    assert "is_hcbi_habit" in fen_loop and "not is_hcbi_habit" in fen_loop, \
        "the points append must be gated on NOT being an hcbi-routed habit"


def test_curly_points_bonus_unaffected_by_hcbi_guard():
    # {N} curly points (0g bonus → column Q) are unrelated to the hcb/hcbi
    # domain-column double-count and must still fire even for an HCBI_HABITS
    # item (e.g. a {N}-annotated "bball" entry should still get its 0g bonus).
    body = _main_body()
    start = body.index("fen_appends = []")
    fen_loop = body[start:body.index("fen_result = None", start)]
    curly_block = fen_loop[fen_loop.index("curly_points"):]
    assert "is_hcbi_habit" not in curly_block, \
        "the {N} curly-points append must not be gated on the hcbi-habit guard"


if __name__ == "__main__":
    import subprocess
    import sys
    sys.exit(subprocess.call(["python3", "-m", "pytest", __file__, "-v"]))
