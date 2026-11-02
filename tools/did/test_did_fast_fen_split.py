#!/usr/bin/env python3
"""Regression: one task, several 分 domains — "[N] @code [N] @code"
(JM 2026-11-01, lib/fen_split.py).

Grammar: each [N] binds to the @code right after it; a bare [N] takes the
task's own label; only the FIRST code is a Todoist label. A single-[N] task
(and the "[40] @家" form mobile janus sends) must behave exactly as before.

Run: python3 -m pytest tools/did/test_did_fast_fen_split.py -v
"""
import importlib.util
import sys
from pathlib import Path

DID_FAST = Path(__file__).parent / "did-fast.py"


def _load_did_fast():
    spec = importlib.util.spec_from_file_location("did_fast_split_under_test", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


df = _load_did_fast()


def _col(code):
    return df._code_fen_col(code)


# ── parse_input ──────────────────────────────────────────────────────────

def test_parse_input_single_bracket_unchanged():
    (it,) = df.parse_input("fix faucet [8]")
    assert it.points_override == 8 and it.points_split is None
    assert it.name == "fix faucet"


def test_parse_input_single_pair_is_not_a_split():
    # mobile janus appends "[total] @project" to every /done: today's form.
    (it,) = df.parse_input("prep call 1000-1030 [40] @家")
    assert it.points_split is None
    assert it.points_override == 40 and it.project_override == "家"


def test_parse_input_split_pairs():
    (it,) = df.parse_input("mock WAU tree [30] @h335 [10] @i9")
    assert it.points_split == [(30, "h335"), (10, "i9")]
    assert it.points_override == 40
    assert it.project_override == "h335"
    assert it.name == "mock WAU tree"


def test_parse_input_split_with_bare_share():
    (it,) = df.parse_input("prep call [20] @m5x2 [10]")
    assert it.points_split == [(20, "m5x2"), (10, None)]
    assert it.points_override == 30 and it.project_override == "m5x2"


def test_parse_input_drops_stray_codes_from_name():
    # A split task's codes ride along in the Toggl description.
    (it,) = df.parse_input("mock WAU tree @h335 @i9 1000-1030")
    assert it.name == "mock WAU tree"
    assert it.project_override == "h335"
    assert it.time_range == ("1000", "1030")


# ── credit resolution ────────────────────────────────────────────────────

def _task(content, labels):
    return {"id": "t1", "content": content, "labels": labels}


def test_task_credit_single_unchanged():
    (it,) = df.parse_input("fix faucet")
    pts, col, extra = df._task_credit(it, _task("fix faucet (30) [8]", ["xk87"]))
    assert (pts, col, extra) == (8, _col("xk87"), [])


def test_task_credit_content_split():
    (it,) = df.parse_input("mock WAU tree")
    pts, col, extra = df._task_credit(it, _task("mock WAU tree (30) [30] @h335 [10] @i9", ["h335"]))
    assert (pts, col) == (30, _col("h335"))
    assert extra == [(_col("i9"), 10)]


def test_task_credit_bare_share_takes_label_and_merges_by_column():
    (it,) = df.parse_input("prep call")
    # i447 and i9 share a column: one append, never two with one idem key.
    pts, col, extra = df._task_credit(it, _task("prep call [20] @i447 [10]", ["i9"]))
    assert (pts, col, extra) == (30, _col("i9"), [])


def test_task_credit_typed_split_wins():
    (it,) = df.parse_input("prep call [5] @m5x2 [5] @hcb")
    pts, col, extra = df._task_credit(it, _task("prep call [20] @i9 [10] @hcmc", ["i9"]))
    assert (pts, col) == (5, _col("m5x2"))
    assert extra == [(_col("hcb"), 5)]


def test_task_credit_total_typed_back_keeps_split():
    # mobile's "[40] @家" on a 30+10 split task: the content's split, not 40 to 家.
    (it,) = df.parse_input("mock WAU tree 1000-1030 [40] @家")
    pts, col, extra = df._task_credit(it, _task("mock WAU tree [30] @h335 [10] @i9", ["h335"]))
    assert (pts, col) == (30, _col("h335"))
    assert extra == [(_col("i9"), 10)]


def test_task_credit_other_total_typed_is_single_column():
    (it,) = df.parse_input("mock WAU tree [25]")
    pts, col, extra = df._task_credit(it, _task("mock WAU tree [30] @h335 [10] @i9", ["h335"]))
    assert (pts, col, extra) == (25, _col("h335"), [])


def test_task_credit_defer_credits_what_is_deducted():
    (it,) = df.parse_input("prep call [15] --tmrw")
    assert it.defer_date
    pts, col, extra = df._task_credit(it, _task("prep call [20] @m5x2 [10]", ["xk87"]))
    assert (pts, col, extra) == (15, _col("m5x2"), [])
    (it2,) = df.parse_input("prep call [25] --tmrw")
    pts, col, extra = df._task_credit(it2, _task("prep call [20] @m5x2 [10]", ["xk87"]))
    assert (pts, col) == (20, _col("m5x2"))
    assert extra == [(_col("xk87"), 5)]


def test_task_credit_unknown_code_falls_back_to_label(capsys):
    (it,) = df.parse_input("fix faucet")
    pts, col, extra = df._task_credit(it, _task("fix faucet [8] @posthoc [2] @i9", ["xk87"]))
    assert (pts, col) == (8, _col("xk87"))
    assert extra == [(_col("i9"), 2)]
    assert "@posthoc" in capsys.readouterr().err


def test_defer_deduction_walks_pairs(monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=15):
        import json
        if req.data:
            seen.update(json.loads(req.data))
        return _Resp()

    monkeypatch.setattr(df.urllib.request, "urlopen", fake_urlopen)
    ok, err = df.defer_todoist_task("t1", "2026-11-02", 25, "prep call [20] @m5x2 [10]")
    assert ok and err is None
    assert seen["content"] == "prep call [0] @m5x2 [5]"
