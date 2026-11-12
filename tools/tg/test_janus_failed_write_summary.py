"""Regression (2026-11-12): did-fast exited 2 because the 0分 batch never
reached the daemon, yet desktop janus flashed "✓ Jon/Sarah connect +100分 ·
todoist ✓" — _did_summary only read results, never the *_write blocks, and
_run_row_did recorded the 100 into the row's local 分 record. The 100 were
never in Excel (巳 showed 98 while the rows summed to 140)."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_janus_points import _load_tui  # noqa: E402

FAILED = ('{"results": [{"name": "Jon/Sarah connect", "step": "todoist", '
          '"0fen": {"col": "T", "points": 100}, "todoist": {"id": "x", "closed": true}}], '
          '"agent_needed": [], "0fen_write": {"ok": false, "error": "ssh: connect timed out"}}')
LANDED = ('{"results": [{"name": "Jon/Sarah connect", "step": "todoist", '
          '"0fen": {"col": "T", "points": 100}, "todoist": {"id": "x", "closed": true}}], '
          '"agent_needed": [], "0fen_write": {"ok": true, "output": "OK:0fen row=314"}}')


def test_summary_says_not_credited_when_the_sheet_write_failed():
    m = _load_tui()
    s = m._did_summary(FAILED)
    assert "✓ Jon/Sarah connect +100分" not in s
    assert "✗ Jon/Sarah connect +100分 NOT credited" in s
    assert "0fen_write: ssh: connect timed out" in s


def test_summary_unchanged_when_the_write_landed():
    m = _load_tui()
    assert m._did_summary(LANDED) == "✓ Jon/Sarah connect +100分 · todoist ✓"


def test_row_points_record_is_gated_on_write_failures():
    src = (Path(__file__).parent / "janus.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_run_row_did")
    body = ast.get_source_segment(src, fn)
    i = body.index("_entry_pts_add(")
    cond = body[body.rindex("if ", 0, i):i]
    assert "janus_done.write_failures(data)" in cond, (
        "_entry_pts_add must not record 分 for a run whose sheet write failed")
