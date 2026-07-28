"""Regression tests for the excel-http audit ledger (chain logic only — no Excel)."""

import importlib.util
import json
import os

import pytest

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("server", os.path.join(HERE, "server.py"))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LEDGER_DIR", str(tmp_path))
    server.CHAIN_INDEX.clear()
    return tmp_path


def test_chain_key_prefers_date_over_row():
    assert server.chain_key("0分", "R", "7/28", 207) == ("0分", "R", "7/28")
    assert server.chain_key("1n+", "AI", None, 35) == ("1n+", "AI", "r35")


def test_journal_and_seed_roundtrip(ledger):
    server.journal({"ts": "2026-07-28T10:00:00", "kind": "append", "sheet": "0分",
                    "col": "R", "row": 207, "date": "7/28", "value": "+30",
                    "before": "=1+2", "after": "=1+2+30"})
    server.CHAIN_INDEX.clear()
    server.seed_chain_index()
    assert server.CHAIN_INDEX[("0分", "R", "7/28")] == "=1+2+30"


def test_check_chain_states(ledger):
    key = ("0分", "R", "7/28")
    assert server.check_chain(key, "=anything")[0] == "new"
    server.CHAIN_INDEX[key] = "=1+2+30"
    assert server.check_chain(key, "=1+2+30")[0] == "ok"
    state, expected = server.check_chain(key, "=1+2+99")
    assert state == "broken" and expected == "=1+2+30"


def test_check_chain_rescans_for_fallback_writes(ledger):
    """A fallback write (daemon was down, client journaled directly) advances
    the chain on disk behind the in-memory index — must NOT flag broken."""
    key = ("0分", "R", "7/28")
    server.CHAIN_INDEX[key] = "=1+2"
    with open(server.ledger_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "append", "sheet": "0分", "col": "R",
                            "row": 207, "date": "7/28", "before": None,
                            "after": "=1+2+5", "fallback": True}) + "\n")
    assert server.check_chain(key, "=1+2+5")[0] == "ok"
    assert server.CHAIN_INDEX[key] == "=1+2+5"


def test_iter_ledger_tolerates_torn_line(ledger):
    p = server.ledger_path()
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"after": "=1", "sheet": "0分", "col": "R", "date": "7/28"}) + "\n")
        f.write('{"after": "=torn...')  # partial trailing line from a live append
    assert len(list(server.iter_ledger(p))) == 1


def test_ack_requires_note():
    assert server.do_ack({"sheet": "0分", "col": "R", "date": "7/28"}) == {
        "ok": False, "error": "ack_requires_note"}
    assert server.do_ack({"sheet": "0分", "col": "R", "note": "  "})["ok"] is False
