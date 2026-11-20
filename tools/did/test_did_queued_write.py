"""Write queue, stage 2 (2026-11-20): excel-http can accept a write it cannot
apply yet (Excel is not answering) and replay it later. did-fast must then
  * treat the write as accepted (rc 0): a failed completion is retried by dtd,
    and the retry is a second write once the queue drains;
  * never run the raw AppleScript fallback for it, for the same reason;
  * not invent an echo: no row, no pre-image. The request id is kept instead,
    and undo-fast gets both from the daemon once the write has landed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
QUEUED = {"ok": False, "queued": True, "rid": "r" * 32, "intent": "i1",
          "error": "queued: Excel is not answering"}


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def df():
    return _load("did_fast_queued", "did-fast.py")


@pytest.fixture(scope="module")
def uf():
    return _load("undo_fast_queued", "undo-fast.py")


def _w0(df, name, col_num, value=1):
    return df.RouteResult(item=SimpleNamespace(name=name), step="0n", col_num=col_num, write_value=value)


# ── did-fast ────────────────────────────────────────────────────────────────

def test_queued_0n_write_is_accepted_without_fallback_or_fake_echo(df, monkeypatch):
    monkeypatch.setattr(df.neon_excel, "batch_write", lambda *a, **k: dict(QUEUED))
    res = df.write_0n_via_daemon([_w0(df, "hiit", 31)], "11/20")
    assert res is not None                         # None is what sends the caller to the raw script
    assert res.returncode == 0 and df._queued_rid(res) == QUEUED["rid"]
    assert df.parse_pre_lines(res.stdout) == {}    # no pre-image is claimed
    assert "OK:row=" not in res.stdout             # and no echo is invented


def test_queued_1n_write_is_accepted_and_has_no_week_row(df, monkeypatch):
    import re
    monkeypatch.setattr(df.neon_excel, "batch_write", lambda *a, **k: dict(QUEUED))
    w = df.RouteResult(item=SimpleNamespace(name="1 hcme"), step="1n", col_letter="X", write_value=1)
    res = df.write_1n_via_daemon([w], "11.3")
    assert res is not None and res.returncode == 0 and df._queued_rid(res) == QUEUED["rid"]
    # undo-fast builds "+'1n+'!<col><week_row>" from this: a sentinel would be written into 0分
    assert re.search(r"weekRow=(\d+)", res.stdout) is None


def test_queued_appends_are_accepted_so_dtd_does_not_retry(df, monkeypatch):
    monkeypatch.setattr(df.neon_excel, "batch_append", lambda *a, **k: dict(QUEUED))
    fen = df.append_0fen_batch([("R", 15)], "11/20", ["task"], "0fen")
    hcbi = df.append_hcbi_via_daemon([{"col": "Y", "value": "+15"}], "11/20", "did bball")
    for res in (fen, hcbi):
        assert res.returncode == 0 and df._queued_rid(res) == QUEUED["rid"]
        assert df._batch_landed(res)


def test_a_plain_failure_is_still_a_failure(df, monkeypatch):
    monkeypatch.setattr(df.neon_excel, "batch_append",
                        lambda *a, **k: {"ok": False, "error": "excel_error"})
    res = df.append_0fen_batch([("R", 15)], "11/20", ["task"], "0fen")
    assert res.returncode == 1 and df._queued_rid(res) is None
    monkeypatch.setattr(df.neon_excel, "batch_write",
                        lambda *a, **k: {"ok": False, "error": "daemon_unreachable", "fallback": True})
    assert df.write_0n_via_daemon([_w0(df, "hiit", 31)], "11/20") is None     # daemon down → raw script


def test_main_flow_credits_a_queued_1n_habit_and_never_fails_on_queued(df):
    """Source-level: the 4c 0分 reference append must not be gated on a week
    row a queued write cannot have, and a queued 0n write must not be judged
    by the echo verifier (it would report 'no OK:row echo' and exit 2)."""
    src = (HERE / "did-fast.py").read_text(encoding="utf-8")
    assert "if one_n_writes and (week_row or queued_1n_cols):" in src
    assert "r.col_letter not in queued_1n_cols" in src
    assert "if on_result.returncode == 0 and not _queued_rid(on_result) else None" in src
    out = {"0n_write": {"ok": True, "output": "QUEUED:x", "queued": "x"}}
    assert df._write_failures(out) == []


# ── undo-fast ───────────────────────────────────────────────────────────────

def _out():
    return {
        "results": [
            {"name": "hiit", "step": "0n", "col": 31, "value": 1, "0fen": {"col": "W", "points": 10}},
            {"name": "1 hcme", "step": "1n", "col_letter": "X", "week_row": None, "fen_col": "V"},
        ],
        "0n_write": {"ok": True, "output": "QUEUED:a", "queued": "a"},
        "1n_write": {"ok": True, "output": "QUEUED:b", "queued": ["b"]},
        "0fen_write": {"ok": True, "output": "QUEUED:c", "queued": "c"},
    }


def test_undo_waits_while_its_write_is_still_queued(uf, monkeypatch):
    monkeypatch.setattr(uf.excel, "intent_status", lambda rid: {"ok": True, "state": "pending"})
    assert "still queued" in uf.hydrate_queued(_out())
    monkeypatch.setattr(uf.excel, "intent_status", lambda rid: {})
    assert "unreachable" in uf.hydrate_queued(_out())


def test_undo_takes_pre_images_and_week_row_from_the_landed_write(uf, monkeypatch):
    done = {
        "a": {"ok": True, "row": 324, "results": [{"ok": True, "col": "AE", "before_value": "missing value"}]},
        "b": {"ok": True, "row": 51, "results": [{"ok": True, "col": "X", "before": "", "before_value": ""}]},
        "c": {"ok": True, "row": 321, "results": [{"ok": True, "col": "W"}]},
    }
    monkeypatch.setattr(uf.excel, "intent_status",
                        lambda rid: {"ok": True, "state": "done", "result": done[rid]})
    out = _out()
    assert uf.hydrate_queued(out) is None
    assert out["results"][0]["undo"] == {"prev_0n": ""}                     # col 31 = AE
    assert out["results"][1]["week_row"] == "51"
    assert out["results"][1]["undo"] == {"prev_1n_formula": ""}


def test_a_queued_write_that_never_landed_is_not_reversed(uf, monkeypatch):
    monkeypatch.setattr(uf.excel, "intent_status", lambda rid: (
        {"ok": True, "state": "rejected", "result": {"ok": False, "error": "date_not_found"}} if rid == "c"
        else {"ok": True, "state": "held", "note": "cannot tell"}))
    out = _out()
    assert uf.hydrate_queued(out) is None
    # not-ok: reverse_didfast_output will not strip a +10 that was never added
    assert out["0fen_write"]["ok"] is False and out["0n_write"]["ok"] is False
    assert "undo" not in out["results"][0]


def test_ctrl_z_leaves_the_record_in_the_journal_while_anything_is_queued(uf, tmp_path, monkeypatch):
    j = tmp_path / "undo.jsonl"
    rec = {"type": "done", "date": date.today().isoformat(), "names": ["hiit"], "output": {"results": []}}
    j.write_text(json.dumps(rec) + "\n")
    monkeypatch.setattr(uf, "reverse_record", lambda r, e: pytest.fail("must not reverse while queued"))
    monkeypatch.setattr(uf.excel, "queue_pending", lambda: 2)
    res = uf.journal_pop_and_reverse(str(j), None, None, None)
    assert "2 write(s) still queued" in res["error"]
    assert json.loads(j.read_text().strip()) == rec                          # not popped
    # drained: the same ctrl-z now goes through
    monkeypatch.setattr(uf.excel, "queue_pending", lambda: 0)
    reversed_ = []
    monkeypatch.setattr(uf, "reverse_record", lambda r, e: reversed_.append(r))
    monkeypatch.setattr(uf, "clean_filter_files", lambda *a, **k: None)
    assert uf.journal_pop_and_reverse(str(j), None, None, None)["ok"] and reversed_ == [rec]


# ── run.py ──────────────────────────────────────────────────────────────────

def test_run_py_never_treats_a_failed_read_as_zero():
    """old + N written as a whole-cell SET: with reads refused while writes are
    queued, a failed read counted as 0 would queue a SET that erases the cell."""
    src = (HERE / "run.py").read_text(encoding="utf-8")
    assert src.count('if cur.get("ok") is False:') == 3
    assert 'if row5.get("ok") is False:' in src
    assert "if excel.is_queued(result):" in src
