#!/usr/bin/env python3
"""Undo and N color (0n!AF): AF means 0t and 0l are both done, so it clears only
when an undo actually leaves one of them undone. Undoing a 0l re-log leaves S
filled, and the first stamp stands (2026-11-06)."""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("undo_fast", HERE / "undo-fast.py")
uf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uf)


@pytest.fixture
def cells(monkeypatch):
    def _cells(q, s, af):
        vals = {"Q": q, "S": s, "AF": af}
        calls = []
        monkeypatch.setattr(uf.excel, "read",
                            lambda sheet, col, date=None, row=None: {"ok": True, "value": vals[col]})
        monkeypatch.setattr(uf.excel, "batch_write",
                            lambda sheet, writes, **kw: (calls.append(writes), {"ok": True})[1])
        return calls
    return _cells


def test_undoing_0l_to_blank_clears_af(cells):
    calls = cells("1.0", "", "945.0")
    uf.clear_stale_n_color([(19, "")], "11/6", [], "undo 0l")
    assert calls == [[{"col": "AF", "value": ""}]]


def test_undoing_a_0l_relog_keeps_the_first_stamp(cells):
    calls = cells("1.0", "12.0", "613.0")
    uf.clear_stale_n_color([(19, "12.0")], "11/6", [], "undo 0l")
    assert calls == []


def test_undoing_an_unrelated_habit_reads_nothing(monkeypatch):
    monkeypatch.setattr(uf.excel, "read", lambda *a, **k: pytest.fail("no read expected"))
    uf.clear_stale_n_color([(31, "1.0")], "11/6", [], "undo hiit")


def test_a_failed_read_leaves_af_alone(monkeypatch):
    monkeypatch.setattr(uf.excel, "read", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(uf.excel, "batch_write", lambda *a, **k: pytest.fail("no write expected"))
    errors = []
    uf.clear_stale_n_color([(19, "")], "11/6", errors, "undo 0l")
    assert errors
