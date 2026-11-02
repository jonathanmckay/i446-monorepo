"""Enter on a query that matches no row and carries a [N] value creates that
task (2026-11-01: "if I enter a task that isn't a match to something in the
fzf box and has points [xx] that gets translated as a /todo").

The router is exercised with a stubbed todo-fast.py, so nothing reaches
Todoist; the parser is tested directly with the real module.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_dtd_schedule_screen import _calls, _gen, _run  # noqa: E402


def _todo_stub(tmp, delay=0.0, fail=False):
    log = tmp / "calls.log"
    stub = tmp / "todo-stub.py"
    stub.write_text(
        "import sys, time\n"
        f"open({str(log)!r}, 'a').write('todo|' + '|'.join(sys.argv[1:]) + '\\n')\n"
        f"time.sleep({delay})\n"
        + ("sys.exit('boom')\n" if fail else "print('+ ' + sys.argv[1])\n"))
    return stub


def _gen_router(tmp, **kw):
    stub = _todo_stub(tmp, **kw)
    extra = f'DTD_TODO="{stub}"; DTD_QUERY="{tmp}/query"; DTD_EVENTS="{tmp}/events"'
    return _gen(tmp, "DTD_PICKENTER", "PICKENTEREOF", extra=extra)


def _wait_log(log, n, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if len(_calls(log)) >= n:
            return
        time.sleep(0.05)


def test_no_match_with_points_creates_the_task():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen_router(tmp)
        assert _run(s, "", "buy milk (10) [5] @家") == "", "transform stdout must stay empty"
        _wait_log(log, 1)
        assert _calls(log) == [["todo", "buy milk (10) [5] @家"]]
        hdr = (tmp / "hdr").read_text()
        assert "buy milk (10) [5] @家" in hdr      # optimistic "＋ adding" or the stub's "+ ..." line


def test_no_match_without_points_does_nothing():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen_router(tmp)
        for q in ("buy milk", "buy milk (10)", "a [12/m] b", ""):
            assert _run(s, "", q) == ""
        time.sleep(0.3)
        assert _calls(log) == []
        assert (tmp / "hdr").read_text() == ""


def test_matched_row_still_starts_even_with_points_in_query():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen_router(tmp)
        _run(s, "T1", "milk [5]")
        time.sleep(0.3)
        assert _calls(log) == [[str(tmp / "enter-stub"), "", "T1"]]


def test_schedule_screen_never_creates():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen_router(tmp)
        (tmp / "blockpick").write_text("A1\n")
        _run(s, "", "buy milk [5]")
        time.sleep(0.3)
        assert _calls(log) == []


def test_double_enter_within_10s_creates_once():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen_router(tmp)
        _run(s, "", "buy milk [5]")
        _run(s, "", "buy milk [5]")
        _run(s, "", "buy eggs [5]")          # different text is a second task
        _wait_log(log, 2)
        time.sleep(0.3)
        assert [c[1] for c in _calls(log)] == ["buy milk [5]", "buy eggs [5]"]


def test_router_returns_before_the_post_finishes():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen_router(tmp, delay=2.0)
        t0 = time.time()
        _run(s, "", "slow task [5]")
        assert time.time() - t0 < 1.0, "the Todoist call must be detached (fzf Pause threshold is 1s)"
        _wait_log(log, 1)


def test_failure_lands_in_header():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s, log = _gen_router(tmp, fail=True)
        _run(s, "", "bad task [5]")
        _wait_log(log, 1)
        t0 = time.time()
        while time.time() - t0 < 3 and "✗" not in (tmp / "hdr").read_text():
            time.sleep(0.05)
        assert "✗ todo failed" in (tmp / "hdr").read_text()


# --- parser ---------------------------------------------------------------------

def _mod():
    spec = importlib.util.spec_from_file_location("todo_fast", HERE / "todo-fast.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_parse_matches_the_todo_skill():
    p = _mod().parse
    assert p("buy milk (10) [5] @家") == {"content": "buy milk (10) [5]", "labels": ["家"], "value": 5}
    assert p("/todo charge phone [3] 戌") == {"content": "charge phone [3]", "labels": ["戌"], "value": 3}
    assert p("read https://example.com/x (20) [8]")["content"] == "read [(link)](https://example.com/x) (20) [8]"
    # 睡觉 is a word, not a block glyph
    assert p("log 睡觉 [2]") == {"content": "log 睡觉 [2]", "labels": [], "value": 2}


def test_parse_multi_domain_split_keeps_pairs_and_first_label():
    r = _mod().parse("mock WAU tree (30) [30] @h335 [10] @i9 @urgent")
    assert r["content"] == "mock WAU tree (30) [30] @h335 [10] @i9"
    assert r["labels"] == ["h335", "urgent"]
    assert r["value"] == 40


def test_cli_parse_creates_nothing():
    out = subprocess.run([sys.executable, str(HERE / "todo-fast.py"), "--parse", "x [1] @i9"],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == {"content": "x [1]", "labels": ["i9"], "value": 1}


def test_create_posts_due_today_and_refreshes_cache(monkeypatch):
    m = _mod()
    seen = {}

    class _T:
        @staticmethod
        def create_task(content, *, labels=None, due_string=None, **kw):
            seen.update(content=content, labels=labels, due=due_string)
            return {"id": "NEW1", "content": content}
    monkeypatch.setitem(sys.modules, "todoist", _T)
    spawned = []
    monkeypatch.setattr(m.subprocess, "Popen", lambda args, **kw: spawned.append(args))
    r = m.create("buy milk (10) [5] @家")
    assert seen == {"content": "buy milk (10) [5]", "labels": ["家"], "due": "today"}
    assert r["task"]["id"] == "NEW1"
    assert spawned and spawned[0][-1] == "--refresh-cache"
