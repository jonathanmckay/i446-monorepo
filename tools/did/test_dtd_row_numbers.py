"""dtd row numbers (2026-10-24: 'add a display number to every line in dtd so I
can reference it from elsewhere ... the order it displays in, not something
intrinsic to the task, just so I can reference it from cc'). The list prints
'NNN ' before every row, writes the same order to ~/.local/state/jm/dtd-order.json,
and agent_claims resolves a bare number ('/d 5') to the task on that row."""
import importlib.util
import json
import time
from pathlib import Path

HERE = Path(__file__).parent
DTD = (HERE / "dtd.sh").read_text()
_SPEC = importlib.util.spec_from_file_location("ac_rows", HERE / "agent_claims.py")
ac = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ac)


def test_every_printed_row_leads_with_its_code_not_a_number():
    """2026-11-15: the display position became the task's two-letter code
    (lib/dtd_codes), published per host so a session elsewhere resolves it."""
    assert "for l in _active + _claims + normal_lines + skipped_lines:" in DTD
    assert "_codes = _dc.render_codes(_dc.queue_tasks(d))" in DTD
    assert "print('\\x1b[2m' + (_pad(_cd) if _cd else '  ') + '\\x1b[0m ' + l)" in DTD
    assert "{_rn:>3}" not in DTD
    # the code eats 3 columns (two cells + a space), so rows right-justify 3 narrower
    assert "ROWNUM_W = 3" in DTD and "cols = max(20, cols - ROWNUM_W)" in DTD


def test_list_writes_the_shared_order_for_claude_sessions():
    i = DTD.index("_op = _os.environ.get('DTD_ORDER', '')")
    block = DTD[i:i + 700]
    assert "'dtd-order.json'" in block and "'ids': _order" in block and "_os.replace(_so + '.tmp', _so)" in block


def _order(tmp_path, ids, age=0):
    p = tmp_path / "dtd-order.json"
    p.write_text(json.dumps({"ts": time.time() - age, "dtd_id": "x", "ids": ids}))
    return p


def test_row_task_id_maps_a_number_to_the_row(tmp_path):
    p = _order(tmp_path, ["A", "B", "C"])
    assert ac.row_task_id(1, p) == "A" and ac.row_task_id(3, p) == "C"
    assert ac.row_task_id(0, p) is None and ac.row_task_id(4, p) is None


def test_a_stale_render_is_not_trusted(tmp_path):
    p = _order(tmp_path, ["A"], age=7 * 3600)
    assert ac.row_task_id(1, p) is None


def test_candidates_resolves_a_bare_row_number(tmp_path, monkeypatch):
    p = _order(tmp_path, ["t2", "t1"])
    monkeypatch.setattr(ac, "DTD_ORDER_FILE", p)
    monkeypatch.setattr(ac, "row_task_id", lambda n, path=p, now=None: ["t2", "t1"][n - 1])

    class DF:
        import re
        ANNOT_RE = re.compile(r"\s*(\(\d+\)|\[\d+\])")

        @staticmethod
        def match_todoist_task(q, tasks):
            return None
    tasks = [{"id": "t1", "content": "notes (3) [8]"}, {"id": "t2", "content": "hiit (10) [23]"}]
    assert ac.candidates("1", tasks, DF) == [tasks[1]]
    assert ac.candidates("2", tasks, DF) == [tasks[0]]
    assert ac.candidates("hiit", tasks, DF) == [tasks[1]]       # names still work


def test_an_unresolvable_row_number_never_matches_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "row_task_id", lambda n, path=None, now=None: None)

    class DF:
        import re
        ANNOT_RE = re.compile(r"\s*(\(\d+\)|\[\d+\])")

        @staticmethod
        def match_todoist_task(q, tasks):
            return tasks[0]
    assert ac.candidates("5", [{"id": "x", "content": "5k run"}], DF) == []


def test_hook_does_not_create_a_task_named_after_a_number():
    src = (HERE / "agent_claims.py").read_text()
    i = src.index("explicit_new = query.lower().startswith(\"new \")")
    block = src[i:i + 2000]
    assert block.index("query.isdigit() and len(query) <= 3") < block.index("task = new_task(name)")
    assert "nothing claimed" in block
