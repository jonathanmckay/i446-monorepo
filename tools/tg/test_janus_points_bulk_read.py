"""Bug 2026-10-12: "I still don't see neon points for the day".

fetch_points() found today's 0分 row by reading B2..B500 one cell per Apple
Event, then G:Y one cell per event (~300 events). With Excel busy on ix that
took ~80s, blew the 15s subprocess timeout on every read, and the abandoned
remote osascript kept Excel loaded for the next one: the header and every
block showed no 分 at all. The script must read ranges in bulk.
"""
import ast
import re
from pathlib import Path

SRC = (Path(__file__).parent / "janus.py").read_text()


def _fetch_points_script() -> str:
    tree = ast.parse(SRC)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fetch_points")
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "script" for t in node.targets):
            return ast.get_source_segment(SRC, node.value)
    raise AssertionError("no script = ... in fetch_points")


def test_row_lookup_reads_column_in_one_event():
    s = _fetch_points_script()
    assert 'range "B1:B500"' in s
    assert not re.search(r'range \("B" & i\)', s), "per-cell row scan is back"


def test_row_cells_read_as_ranges_not_cell_by_cell():
    s = _fetch_points_script()
    assert "cell c of row" not in s, "per-cell G:Y reads are back"
    for rng in ('("G" & r & ":O" & r)', '("P" & r & ":Y" & r)'):
        assert rng in s
