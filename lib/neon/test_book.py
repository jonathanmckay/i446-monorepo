"""lib/neon/book.py — seed + ledger replay. The live gate is
tools/neon-ledger/book_check.py; these pin the replay rules."""
from __future__ import annotations

import datetime as dt
import pickle

import pytest

from neon import book, calc, ledger
from neon.calc import col_index as ci

D = calc.serial(dt.date(2026, 11, 22))


@pytest.fixture()
def seedfile(tmp_path):
    cells = {("0分", ci("B"), 5): D - 1, ("0分", ci("B"), 6): "=B5+1", ("0分", ci("D"), 6): "=SUM(P6:Q6)",
             ("0分", ci("P"), 6): "=13", ("0n", ci("C"), 9): D, ("0n", ci("T"), 1): "0g",
             ("1分+1s", ci("A"), 46): 11.2, ("1分+1s", ci("A"), 47): "=A46+0.1"}
    meta = {"version": book.SEED_VERSION, "src": "x", "saved": "2026-11-22T07:50:45",
            "src_mtime": dt.datetime(2026, 11, 22, 7, 50, 45).timestamp(), "cells": len(cells), "formulas": 4}
    p = tmp_path / "seed.pickle"
    p.write_bytes(pickle.dumps({"meta": meta, "cells": cells, "arrays": {}, "cached": {}}))
    return p


@pytest.fixture()
def db(tmp_path):
    c = ledger.connect(tmp_path / "neon.db")
    yield c
    c.close()


def _w(db, ts, sheet, col, row, value, after, kind="write", **kw):
    e = {"ts": ts, "kind": kind, "sheet": sheet, "col": col, "row": row, "date": "11/22", "value": value,
         "before": "", "after": after, "after_value": "", "before_value": "", "src": "t", "chain": "ok"}
    e.update(kw)
    ledger.record(e, db)


def test_replay_applies_whole_cell_contents_in_order(seedfile, db):
    _w(db, "2026-11-22T07:52:00", "0分", "P", 6, "=13+4", "=13+4", after_value="17.0")
    _w(db, "2026-11-22T07:53:00", "0分", "Q", 6, "+10", "=0+10", kind="append", after_value="10.0")
    _w(db, "2026-11-22T07:54:00", "0分", "P", 6, "=13+7", "=13+7", after_value="20.0")
    b = book.load(seedfile, conn=db)
    assert b.applied == 3 and b.through == "2026-11-22T07:54:00"
    assert b.value("0分", ci("D"), 6) == 30.0           # the dependent total moved with the writes
    early = book.load(seedfile, conn=db, upto="2026-11-22T07:52:30")
    assert early.value("0分", ci("D"), 6) == 17.0


def test_writes_before_the_margin_are_already_in_the_seed(seedfile, db):
    _w(db, "2026-11-22T07:40:00", "0分", "P", 6, "=99", "=99", after_value="99.0")
    assert book.load(seedfile, conn=db).value("0分", ci("P"), 6) == 13.0


def test_a_cleared_cell_is_absent_and_text_stays_text(seedfile, db):
    _w(db, "2026-11-22T08:00:00", "0分", "P", 6, "", "")
    _w(db, "2026-11-22T08:00:01", "0n", "AK", 9, "12", "12", kind="write", mode="text")
    _w(db, "2026-11-22T08:00:02", "0n", "T", 9, "1", "1", after_value="1.0")
    b = book.load(seedfile, conn=db)
    assert b.content("0分", ci("P"), 6) is None and b.value("0分", ci("D"), 6) == 0.0
    assert b.value("0n", ci("T"), 9) == 1.0
    assert b.value("0n", ci("AK"), 9) in ("12", 12.0)   # text mode keeps what the daemon read back


def test_lookups_match_excel(seedfile, db):
    b = book.load(seedfile, conn=db)
    assert book.row_for_date(b, "0分", dt.date(2026, 11, 22)) == 6      # a =B5+1 date chain
    assert book.row_for_date(b, "0n", dt.date(2026, 11, 22)) == 9
    assert book.row_for_week(b, "1分+1s", "11.3") == 47               # 11.299999999999999 reads 11.3
    assert book.header_cols(b, "0n") == {"0g": "T"}
    assert book.row_values(b, "0分", 6, "P", "Q") == {"P": 13.0}


def test_no_seed_and_no_ledger(tmp_path, seedfile):
    assert book.load(tmp_path / "nope.pickle") is None
    b = book.load(seedfile, db_path=tmp_path / "nope.db")
    assert b.applied == 0 and b.value("0分", ci("D"), 6) == 13.0
    assert not (tmp_path / "nope.db").exists()
