#!/usr/bin/env python3
"""Focused tests for did-fast's excel-http 0分 write path.

2026-07-28 migration: did-fast was the last bypass writer of the 0分 sheet
(its batch AppleScript path wrote +15+10 to 0分!R invisibly on 7/27). All
0分 writes now go through lib/neon/excel — batch appends via
append_0fen_batch (one daemon round-trip), the ritual P credit via
neon_excel.read/neon_excel.write — so every write is journaled with a `src`
label and chain-checked. These tests mock the client and pin:
  (a) the batch path passes the right (col, value) pairs and a src label;
  (b) 1n+ cell-reference values ("+'1n+'!K5") pass through unchanged;
  (c) chain-broken responses warn on stderr without raising;
plus the legacy caller-visible result shapes (OK:/ERROR:/rc!=0).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("did_fast_0fen", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_SPEC)
sys.modules["did_fast_0fen"] = df
_SPEC.loader.exec_module(df)  # type: ignore[union-attr]


@pytest.fixture()
def batch_calls(monkeypatch):
    """Mock neon_excel.batch_append, recording calls; default happy response."""
    calls = []

    def fake_batch(sheet, appends, *, date=None, row=None, src=None):
        calls.append({"sheet": sheet, "appends": appends, "date": date, "src": src})
        resp = getattr(fake_batch, "response", None)
        if resp is not None:
            return resp
        return {"ok": True, "row": 210,
                "results": [{"ok": True, "col": c, "chain": "ok"}
                            for c, _ in appends]}

    monkeypatch.setattr(df.neon_excel, "batch_append", fake_batch)
    fake_batch.calls = calls
    return fake_batch


# ── (a) batch completion path: (col, value) pairs + src ─────────────────────

def test_batch_passes_col_value_pairs_and_src(batch_calls):
    res = df.append_0fen_batch([("R", 15), ("Q", 10)], "7/27",
                               ["push", "hiit"], "0fen")
    call = batch_calls.calls[0]
    assert call["sheet"] == "0分"
    assert call["appends"] == [("R", "+15"), ("Q", "+10")]
    assert call["date"] == "7/27"
    assert call["src"] == "did batch: push,hiit"
    # Legacy caller-visible shape preserved (output builder reads these).
    assert res.returncode == 0
    assert res.stdout == "OK:0fen row=210"


def test_single_item_src_is_did_name(batch_calls):
    df.append_0fen_batch([("W", 48)], "7/27", ["hiit"], "0fen")
    assert batch_calls.calls[0]["src"] == "did hiit"


def test_src_names_deduped(batch_calls):
    # One item can produce two appends (domain col + curly Q) — the src
    # label must not repeat the name.
    df.append_0fen_batch([("W", 30), ("Q", 20)], "7/27",
                         ["hiit", "hiit"], "0fen")
    assert batch_calls.calls[0]["src"] == "did hiit"


# ── (b) 1n+ cell-reference appends pass through unchanged ───────────────────

def test_1n_cell_reference_value_passes_through(batch_calls):
    res = df.append_0fen_batch([("S", "+'1n+'!K5")], "7/27",
                               ["1 m5x2"], "1n_0fen")
    call = batch_calls.calls[0]
    assert call["appends"] == [("S", "+'1n+'!K5")]  # no re-quoting, no "+" wrap
    assert call["src"] == "did 1 m5x2"
    assert res.returncode == 0
    assert res.stdout == "OK:1n_0fen row=210"


# ── (c) chain-broken → stderr warning, no raise, operation still ok ─────────

def test_chain_broken_warns_stderr_without_failing(batch_calls, capsys):
    batch_calls.response = {
        "ok": True, "row": 210,
        "results": [{"ok": True, "col": "R", "chain": "broken"},
                    {"ok": True, "col": "Q", "chain": "ok"}],
        "chain_broken_cols": ["R"],
    }
    res = df.append_0fen_batch([("R", 15), ("Q", 10)], "7/27", ["push"], "0fen")
    assert res.returncode == 0 and res.stdout == "OK:0fen row=210"
    err = capsys.readouterr().err
    assert "⚠ 0分!R chain broken — cell modified outside daemon" in err
    # Deduped across chain_broken_cols and per-result chain fields; Q intact.
    assert err.count("chain broken") == 1


def test_warn_chain_broken_single_write_response(capsys):
    # Shape of a /write (ritual P credit) response with a broken chain.
    df._warn_chain_broken({"ok": True, "row": 210, "col": "P",
                           "chain": "broken"})
    assert "⚠ 0分!P chain broken" in capsys.readouterr().err


def test_no_warning_when_chain_ok(batch_calls, capsys):
    df.append_0fen_batch([("R", 15)], "7/27", ["push"], "0fen")
    assert "chain broken" not in capsys.readouterr().err


# ── legacy error-shape preservation ─────────────────────────────────────────

def test_date_not_found_maps_to_legacy_error_string(batch_calls):
    batch_calls.response = {"ok": False,
                            "error": "date_not_found_or_missing_target"}
    res = df.append_0fen_batch([("R", 15)], "7/27", ["push"], "0fen")
    # Old AppleScript returned rc 0 with an ERROR: string — callers key off it.
    assert res.returncode == 0
    assert res.stdout == "ERROR: date 7/27 not found in 0分"


def test_daemon_failure_surfaces_as_nonzero_rc(batch_calls):
    batch_calls.response = {"ok": False, "error": "excel_not_running"}
    res = df.append_0fen_batch([("R", 15)], "7/27", ["push"], "0fen")
    assert res.returncode != 0
    assert "excel_not_running" in res.stderr


def test_client_exception_surfaces_as_nonzero_rc(monkeypatch):
    def boom(*a, **k):
        raise OSError("ssh wedged")
    monkeypatch.setattr(df.neon_excel, "batch_append", boom)
    res = df.append_0fen_batch([("R", 15)], "7/27", ["push"], "0fen")
    assert res.returncode != 0
    assert "ssh wedged" in res.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
