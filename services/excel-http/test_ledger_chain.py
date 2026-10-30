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


def test_ritual_p_write_with_date_chains_after_date_keyed_reconcile(ledger, monkeypatch):
    """2026-10-24..30: 0分!P went "broken" on the first ritual of nearly every
    block. The build-order daemon's block-turnover -1n reconcile writes P
    date-addressed (key 10/27); did-fast's ritual write was row-only (key
    r298), so its chain check compared against the previous RITUAL entry
    and missed the reconcile in between, though the before-formula matched
    the reconcile's after exactly. A write carrying row AND date (did-fast's
    fix) chains under the shared date key."""
    monkeypatch.setattr(server, "neon_ledger", None)  # never touch the SQLite ledger

    def entry(**kw):
        base = {"ts": "2026-10-27T12:00:00", "kind": "write", "sheet": "0分",
                "col": "P", "row": 298}
        base.update(kw)
        return base

    server.journal(entry(date=None, before="=13+13+13+1", after="=13+13+13+4",
                         src="ritual 午 -1n"))
    server.journal(entry(date="10/27", before="=13+13+13+4", after="=13+13+13+7",
                         src="block-turnover -1n reconcile"))

    # Old shape (row only): falsely broken, expected = the stale ritual entry.
    old = server._journal_and_respond(
        "write", {"sheet": "0分", "col": "P", "row": 298,
                  "value": "=13+13+13+7+1", "src": "ritual 未 -1n"},
        298, "=13+13+13+7", "47.0", "=13+13+13+7+1")
    assert old["chain"] == "broken" and old["chain_expected"] == "=13+13+13+4"

    server.journal(entry(date="10/27", before="=13+13+13+7+1", after="=13+13+13+7+13",
                         src="block-turnover -1n reconcile"))
    # Fixed shape (row + date): ok against the reconcile's after.
    new = server._journal_and_respond(
        "write", {"sheet": "0分", "col": "P", "row": 298, "date": "10/27",
                  "value": "=13+13+13+7+13+1", "src": "ritual 申 -1n"},
        298, "=13+13+13+7+13", "60.0", "=13+13+13+7+13+1")
    assert new["chain"] == "ok"
    # ...and the next reconcile (date-keyed) chains after the ritual write.
    assert server.check_chain(("0分", "P", "10/27"), "=13+13+13+7+13+1")[0] == "ok"
