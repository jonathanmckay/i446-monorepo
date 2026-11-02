"""JM 2026-11-01: ⌥↵ on a finished entry keeps the entry's tags on the
recreated Toggl entry, a rate answer at the points prompt passes --rate, and
the did-fast summary names the block each split segment landed in."""
import ast
import json
from pathlib import Path

from test_janus_self_restart import _load

HERE = Path(__file__).parent
SRC = (HERE / "janus.py").read_text()


def test_did_summary_names_the_block_of_each_split_segment():
    mod = _load()
    blob = json.dumps({"results": [
        {"name": "walk", "step": "variable", "0fen": {"col": "W", "points": 42}, "block": "巳",
         "split": {"part": 0, "of": 2, "range": "0918-1000"}},
        {"name": "walk", "step": "variable", "0fen": {"col": "W", "points": 120}, "block": "午",
         "split": {"part": 1, "of": 2, "range": "1000-1200"}},
        {"name": "memo", "step": "todoist", "0fen": {"col": "R", "points": 10}, "block": "午"},
    ]})
    out = mod._did_summary(blob)
    assert "✓ walk +42分 →巳" in out and "✓ walk +120分 →午" in out
    assert "✓ memo +10分  " in out or out.endswith("✓ memo +10分")  # no block on an unsplit item


def test_done_dispatchers_take_flags_and_the_prompt_passes_rate():
    tree = ast.parse(SRC)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ("_run_row_did", "_run_done_command"):
        assert "flags" in [a.arg for a in fns[name].args.args], name
    i = SRC.index("dtgt = STATE.done_target")
    body = SRC[i:SRC.index("    if STATE.points_edit_target is not None:", i)]
    assert "janus_done.is_rate_answer(text)" in body and '"--rate"' in body


def test_finished_entry_command_carries_single_word_tags():
    body = SRC[SRC.index("    suffix = date_sfx\n") - 400:SRC.index("    suffix = date_sfx\n")]
    assert '#{_t}' in body and r'^-?\w+$' in body
