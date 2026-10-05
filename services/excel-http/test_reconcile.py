"""Behavioral tests for the JM Dash source plumbing in excel-http:

- /append and /write journal `before_value`, so a broken chain's Excel edit
  can be measured in points (before_value - previous after_value).
- /reconcile journals outside edits as source 3p-app / via excel, but a cell
  the ledger has never seen gets a sourceless `baseline` (row-template
  formulas must not count as Excel edits).
"""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture
def srv(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("excel_http_t", HERE / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "lookup_row_cached", lambda sheet, date: 276)
    mod.CHAIN_INDEX.clear()
    return mod


def _ledger(srv):
    return list(srv.iter_ledger(srv.ledger_path()))


def test_append_journals_before_value(srv, monkeypatch):
    monkeypatch.setattr(srv, "osascript", lambda s: (0, "=5\t5\t15\t=5+10", ""))
    srv.do_append({"sheet": "0分", "col": "R", "date": "10/5", "value": "+10", "src": "did x"})
    e = _ledger(srv)[-1]
    assert (e["before"], e["before_value"], e["after"], e["after_value"]) == ("=5", "5", "=5+10", "15")


def _cells(*pairs):
    return "".join(f"{v}\t{f}\x1e" for v, f in pairs)


def test_reconcile_baselines_unseen_cells_then_flags_later_edits(srv, monkeypatch):
    # Morning: template formula in W, empty Y. Never seen -> baseline, no source.
    monkeypatch.setattr(srv, "osascript", lambda s: (0, _cells(("65", "=hcbi!AA280+5"), ("", "")), ""))
    out = srv.do_reconcile({"sheet": "0分", "date": "10/5", "cols": ["W", "Y"]})
    assert [c["kind"] for c in out["changes"]] == ["baseline", "baseline"]
    assert all("source" not in e for e in _ledger(srv))

    # Same state again: nothing new to journal.
    out = srv.do_reconcile({"sheet": "0分", "date": "10/5", "cols": ["W", "Y"]})
    assert out["changes"] == []

    # JM types +20 into Y in Excel: reconcile attributes it to 3p-app / excel.
    monkeypatch.setattr(srv, "osascript", lambda s: (0, _cells(("65", "=hcbi!AA280+5"), ("20", "=20")), ""))
    out = srv.do_reconcile({"sheet": "0分", "date": "10/5", "cols": ["W", "Y"]})
    assert out["changes"] == [{"col": "Y", "kind": "reconcile", "before_value": "", "after_value": "20"}]
    e = _ledger(srv)[-1]
    assert (e["source"], e["via"], e["kind"]) == ("3p-app", "excel", "reconcile")


def test_reconcile_advances_chain_so_next_write_is_not_broken(srv, monkeypatch):
    monkeypatch.setattr(srv, "osascript", lambda s: (0, _cells(("", "")), ""))
    srv.do_reconcile({"sheet": "0分", "date": "10/5", "cols": ["Y"]})
    monkeypatch.setattr(srv, "osascript", lambda s: (0, _cells(("20", "=20")), ""))
    srv.do_reconcile({"sheet": "0分", "date": "10/5", "cols": ["Y"]})
    monkeypatch.setattr(srv, "osascript", lambda s: (0, "=20\t20\t30\t=20+10", ""))
    resp = srv.do_append({"sheet": "0分", "col": "Y", "date": "10/5", "value": "+10"})
    assert resp["chain"] == "ok"


def test_reconcile_requires_cols(srv):
    assert srv.do_reconcile({"sheet": "0分", "date": "10/5"})["ok"] is False


def test_reconcile_is_routed(srv):
    assert srv.ROUTES["/reconcile"] is srv.do_reconcile


def test_reconcile_matches_row_addressed_writes(srv, monkeypatch):
    """Ritual -1n writes address P by row (date None). A date-addressed
    reconcile must still see them, not report a phantom Excel edit
    (live 2026-10-05: P flagged 27 -> 40 though the ledger already had 40)."""
    monkeypatch.setattr(srv, "osascript", lambda s: (0, "=1\t1\t40\t=1+39", ""))
    srv.do_write({"sheet": "0分", "col": "P", "row": 276, "value": "=1+39"})
    monkeypatch.setattr(srv, "osascript", lambda s: (0, _cells(("40", "=1+39")), ""))
    out = srv.do_reconcile({"sheet": "0分", "date": "10/5", "cols": ["P"]})
    assert out["changes"] == []
    # A real edit after it is journaled under the row key, so the next
    # row-addressed ritual write chains cleanly.
    monkeypatch.setattr(srv, "osascript", lambda s: (0, _cells(("45", "=1+39+5")), ""))
    out = srv.do_reconcile({"sheet": "0分", "date": "10/5", "cols": ["P"]})
    assert out["changes"][0]["kind"] == "reconcile"
    assert _ledger(srv)[-1]["date"] is None
    monkeypatch.setattr(srv, "osascript", lambda s: (0, "=1+39+5\t45\t48\t=1+39+5+3", ""))
    assert srv.do_write({"sheet": "0分", "col": "P", "row": 276, "value": "=1+39+5+3"})["chain"] == "ok"
