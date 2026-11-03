"""lib/neon/adopt.py — hand-typed hcbi edits adopted into the ledger.

Fixture: testdata/hcbi-hand-2026-10.jsonl, a synthetic hcbi day (row 305,
10/30; no real food or health data) with every way a hand edit reaches the
JSONL: a reconcile observation (AW text, E count), an /ate append whose
`before` carries an unseen edit (AL), an edit a later set overwrote (AN,
moot), a snapshot (P, not an edit) and another day's edit (row 304).
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from neon import adopt, ledger, rules

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "testdata" / "hcbi-hand-2026-10.jsonl"
DAY = "2026-10-30"
NOW = dt.datetime(2026, 10, 30, 23, 58, 0)


@pytest.fixture()
def env(tmp_path):
    ldir = tmp_path / "neon-ledger"
    ldir.mkdir()
    shutil.copy(FIXTURE, ldir / "2026-10.jsonl")
    c = ledger.connect(tmp_path / "neon.db")
    ledger.import_jsonl([ldir / "2026-10.jsonl"], c)
    yield c, ldir
    c.close()


def _lines(ldir):
    return list(ledger.iter_jsonl(ldir / "2026-10.jsonl"))


def _strict(c, col):
    return rules._Book(c, strict=True).get("hcbi", col, 305)


def test_candidates_find_exactly_the_hand_edits():
    entries = list(ledger.iter_jsonl(FIXTURE))
    got = {(x["col"], x["reason"]): x for x in adopt.candidates(entries, [DAY])}
    assert set(got) == {("AL", "append-before"), ("AW", "reconcile"), ("E", "reconcile")}
    al = got[("AL", "append-before")]
    assert al["content"] == "=300+150" and al["ledger_before"] == "=300"
    assert al["ts"] == "2026-10-30T12:59:59"          # lands just before the append
    assert got[("AW", "reconcile")]["ts"] == "2026-10-30T23:55:01"


def test_other_day_only_when_asked():
    entries = list(ledger.iter_jsonl(FIXTURE))
    got = adopt.candidates(entries, ["2026-10-29"])
    assert [(x["col"], x["row"]) for x in got] == [("E", 304)]


def test_norm_treats_numeric_spellings_and_blanks_alike():
    assert adopt.norm("0") == adopt.norm("0.0") == adopt.norm(" 0 ")
    assert adopt.norm("") is None and adopt.norm(None) is None
    assert adopt.norm("=1 + 1") == adopt.norm("=1+1")
    assert adopt.norm("=0") != adopt.norm("0")


def test_run_tags_source_hand_and_strict_sees_the_edits(env):
    c, ldir = env
    assert _strict(c, "AL") == 500.0                  # 300 + 200: the typed +150 is lost
    assert _strict(c, "AW") is None and _strict(c, "E") is None
    out = adopt.run([DAY], conn=c, ledger_dir=ldir, now=NOW)
    assert len(out) == 3
    rows = c.execute("SELECT col, kind, source, src, jsonl_kind FROM events "
                     "WHERE jsonl_kind='adopt' ORDER BY col").fetchall()
    assert rows == [("AL", "set", "hand", "hand-adopt", "adopt"),
                    ("AW", "text", "hand", "hand-adopt", "adopt"),
                    ("E", "set", "hand", "hand-adopt", "adopt")]
    assert _strict(c, "AL") == 650.0                  # = Excel
    assert _strict(c, "AW") == "synthetic soup"
    assert _strict(c, "E") == 2.0
    adopted = [e for e in _lines(ldir) if e.get("kind") == "adopt"]
    assert len(adopted) == 3
    assert all(e["source"] == "hand" and e["after"] is None and e["before"] is None for e in adopted)


def test_idempotent_rerun_and_rebuild(env, tmp_path):
    c, ldir = env
    assert len(adopt.run([DAY], conn=c, ledger_dir=ldir, now=NOW)) == 3
    assert adopt.run([DAY], conn=c, ledger_dir=ldir, now=NOW + dt.timedelta(hours=4)) == []
    assert sum(1 for e in _lines(ldir) if e.get("kind") == "adopt") == 3
    # DB rebuilt from the JSONL (the durable record): nothing new to adopt
    c2 = ledger.connect(tmp_path / "rebuilt.db")
    ledger.import_jsonl([ldir / "2026-10.jsonl"], c2)
    assert adopt.run([DAY], conn=c2, ledger_dir=ldir, now=NOW) == []
    assert rules._Book(c2, strict=True).get("hcbi", "AL", 305) == 650.0
    c2.close()


def test_idem_in_db_alone_blocks_duplicates(env):
    """JSONL line lost (or written elsewhere) but the DB has it: no repeat."""
    c, ldir = env
    adopt.run([DAY], conn=c, ledger_dir=ldir, now=NOW)
    shutil.copy(FIXTURE, ldir / "2026-10.jsonl")      # drop the adopt lines from the file
    assert adopt.run([DAY], conn=c, ledger_dir=ldir, now=NOW) == []


def test_dry_run_writes_nothing(env):
    c, ldir = env
    before = (ldir / "2026-10.jsonl").read_bytes()
    n = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert len(adopt.run([DAY], conn=c, ledger_dir=ldir, now=NOW, dry_run=True)) == 3
    assert (ldir / "2026-10.jsonl").read_bytes() == before
    assert c.execute("SELECT COUNT(*) FROM events").fetchone()[0] == n


def test_excel_view_unchanged_and_writes_view_orders_by_ts(env):
    c, ldir = env
    loose_before = ledger.state(DAY, "hcbi", c)
    adopt.run([DAY], conn=c, ledger_dir=ldir, now=NOW)
    assert ledger.state(DAY, "hcbi", c) == loose_before
    w = ledger.state(DAY, "hcbi", c, writes_only=True)
    assert w["AL"]["value"] == 650.0                  # the 13:00 append, not the 12:59:59 adopt
    assert w["AW"]["text"] == "synthetic soup"


def test_audit_replay_ignores_adopt_lines(env):
    c, ldir = env
    spec = importlib.util.spec_from_file_location(
        "neon_ledger_audit", HERE.parents[1] / "scripts" / "neon-ledger-audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    e0, w0, _ = audit.replay(_lines(ldir))
    adopt.run([DAY], conn=c, ledger_dir=ldir, now=NOW)
    e1, w1, _ = audit.replay(_lines(ldir))
    assert (e1, w1) == (e0, w0)


def test_reconcile_hook_respects_kill_switch(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "neon_reconcile", HERE.parents[1] / "scripts" / "neon-reconcile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("NEON_ADOPT", "0")
    assert mod.adopt_hand_edits([DAY]) == 0


def test_adopt_classifies_as_write_never_points():
    base = {"ts": "2026-10-30T23:55:01", "kind": "adopt", "sheet": "hcbi", "col": "AL",
            "row": 305, "date": "10/30", "after": None, "before": None, "src": "hand-adopt"}
    assert ledger.classify(dict(base, value="=300+150", after_value="450.0"))["kind"] == "set"
    assert ledger.classify(dict(base, value="+15", after_value="15.0"))["points"] is None
    assert ledger.classify(dict(base, value="synthetic", after_value="synthetic"))["kind"] == "text"
    assert json.dumps(adopt.build_entry(
        {"sheet": "hcbi", "col": "E", "row": 305, "date": DAY, "ts": base["ts"], "content": "2",
         "after_value": "2.0", "ledger_before": "", "from_hash": "f" * 40, "reason": "reconcile"},
        NOW))
