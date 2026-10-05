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


# ── Neon audit macro (/observe) and quiet writes ─────────────────────────────

def test_observe_journals_excel_edit_with_macro_before(srv):
    out = srv.do_observe({"sheet": "0分", "ts": "2026-10-05T12:00:00", "cells": [
        {"row": 276, "col": "T", "date": "10/5", "formula": "=1+20", "value": "21",
         "before_formula": "=1", "before_value": "1"}]})
    assert out == {"ok": True, "journaled": 1, "truncated": False}
    e = _ledger(srv)[-1]
    assert (e["kind"], e["source"], e["via"]) == ("excel-edit", "3p-app", "excel")
    assert (e["before_value"], e["after_value"], e["after"]) == ("1", "21", "=1+20")


def test_observe_advances_chain_so_next_pipeline_write_is_ok(srv, monkeypatch):
    srv.do_observe({"sheet": "0分", "ts": "2026-10-05T12:00:00", "cells": [
        {"row": 276, "col": "T", "date": "10/5", "formula": "=1+20", "value": "21"}]})
    monkeypatch.setattr(srv, "osascript", lambda s: (0, "=1+20\t21\t31\t=1+20+10", ""))
    assert srv.do_append({"sheet": "0分", "col": "T", "date": "10/5", "value": "+10"})["chain"] == "ok"


def test_late_observe_does_not_roll_chain_back(srv, monkeypatch):
    """A pipeline write that lands before the macro's (backgrounded) report
    must stay the chain head."""
    monkeypatch.setattr(srv, "osascript", lambda s: (0, "=1+20\t21\t31\t=1+20+10", ""))
    srv.do_append({"sheet": "0分", "col": "T", "date": "10/5", "value": "+10"})
    srv.do_observe({"sheet": "0分", "ts": "2000-01-01T00:00:00", "cells": [
        {"row": 276, "col": "T", "date": "10/5", "formula": "=1+20", "value": "21",
         "before_formula": "=1", "before_value": "1"}]})
    e = _ledger(srv)[-1]
    assert e["after"] is None and e["observed_after"] == "=1+20"
    assert srv.CHAIN_INDEX[srv.chain_key("0分", "T", "10/5", 276)] == "=1+20+10"


def test_observe_skips_unchanged_and_handles_structural(srv):
    out = srv.do_observe({"sheet": "0分", "cells": [
        {"row": 1, "col": "A", "formula": "x", "before_formula": "x"}]})
    assert out["journaled"] == 0
    srv._ROW_CACHE[("0分", "10/5")] = 276
    assert srv.do_observe({"sheet": "0分", "structural": True, "rows": "277:277"})["structural"]
    assert srv._ROW_CACHE == {}


def test_observe_bypasses_excel_lock(srv):
    assert "/observe" in srv.NO_EXCEL


def test_mixed_addressing_is_not_a_broken_chain(srv, monkeypatch):
    """Ritual writes P by row, block-turnover by date: a date-addressed write
    after a row-addressed one must chain ok, not 'broken'."""
    monkeypatch.setattr(srv, "osascript", lambda s: (0, "=1\t1\t2\t=2", ""))
    srv.do_write({"sheet": "0分", "col": "P", "row": 276, "value": "=2"})
    monkeypatch.setattr(srv, "osascript", lambda s: (0, "=2\t2\t3\t=3", ""))
    assert srv.do_write({"sheet": "0分", "col": "P", "date": "10/5", "value": "=3"})["chain"] == "ok"


def test_writes_toggle_events_off_and_back_on(srv, monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "QUIET_LOCK", str(tmp_path / "lock"))
    calls = []

    class R:
        returncode = 0
        stdout = ""
        stderr = ""
    monkeypatch.setattr(srv.subprocess, "run", lambda cmd, **kw: calls.append(cmd[-1]) or R())
    srv.osascript('tell application "Microsoft Excel" to set value of cell "A1" to 1')
    assert "enable events to false" in calls[0] and "enable events to true" in calls[-1]
    calls.clear()
    srv.osascript('tell application "Microsoft Excel" to get value of cell "A1"')
    assert len(calls) == 1  # reads untouched


def test_events_reenabled_even_when_write_times_out(srv, monkeypatch, tmp_path):
    import subprocess
    monkeypatch.setattr(srv, "QUIET_LOCK", str(tmp_path / "lock"))
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[-1])
        if "set value" in cmd[-1]:
            raise subprocess.TimeoutExpired(cmd, 15)

        class R:
            returncode = 0
            stdout = stderr = ""
        return R()
    monkeypatch.setattr(srv.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        srv.osascript('tell application "Microsoft Excel" to set value of cell "A1" to 1')
    assert "enable events to true" in calls[-1]


def test_macro_ping_recorded_for_health(srv):
    assert srv.do_observe({"ping": True})["ping"] is True
    assert srv.MACRO_STATE["last_ping"]


def test_ping_is_skipped_until_macro_installed(srv, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(srv.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(srv, "MACRO_INSTALLED", str(tmp_path / "missing"))
    srv.ping_macro()
    assert calls == []
