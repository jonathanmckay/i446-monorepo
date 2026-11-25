"""tools/neon-ledger/export_xlsx.py — the daily ledger → workbook backup
(Neon分v14-export.xlsx, 2026-11-24)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import re
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from neon import book, ledger  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("export_xlsx", Path(__file__).parent / "export_xlsx.py")
ex = importlib.util.module_from_spec(_SPEC)
sys.modules["export_xlsx"] = ex
_SPEC.loader.exec_module(ex)

SAVED = dt.datetime(2026, 11, 22, 7, 50, 45)


@pytest.fixture()
def src(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "0分"
    ws["B6"], ws["P6"], ws["Q6"], ws["D6"], ws["A7"] = dt.datetime(2026, 11, 22), 13, "=P6*2", "=SUM(P6:Q6)", "note"
    wb.create_sheet("0n")["C9"] = 5
    p = tmp_path / "Neon分v12.2.xlsx"
    wb.save(p)
    os.utime(p, (SAVED.timestamp(), SAVED.timestamp()))
    return p


def _w(db, ts, sheet, col, row, after):
    ledger.record({"ts": ts, "kind": "write", "sheet": sheet, "col": col, "row": row, "date": "11/22",
                   "value": after, "before": "", "after": after, "after_value": "", "before_value": "",
                   "src": "t", "chain": "ok"}, db)


def test_export_is_the_workbook_plus_the_ledgers_writes(src, tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "LOG", tmp_path / "log.jsonl")
    dbp = tmp_path / "neon.db"
    db = ledger.connect(dbp)
    _w(db, "2026-11-22T09:00:00", "0分", "P", 6, "=13+20")      # a credit appended after the save
    _w(db, "2026-11-22T09:05:00", "0n", "D", 9, "hello")         # a cell the workbook never had
    _w(db, "2026-11-22T09:06:00", "0分", "A", 7, "")             # cleared
    db.commit()
    db.close()
    out = tmp_path / "Neon分v14-export.xlsx"
    res = ex.export(src, out, db_path=dbp)
    assert res["published"] and res["writes_applied"] == 3 and res["cells_changed"] == 3, res
    got = openpyxl.load_workbook(out)
    assert got["0分"]["P6"].value == "=13+20"
    assert got["0分"]["Q6"].value == "=P6*2", "untouched formulas survive"
    assert got["0n"]["D9"].value == "hello" and got["0分"]["A7"].value is None
    assert got["0分"]["B6"].is_date, "number formats survive: a date still reads as a date"
    assert openpyxl.load_workbook(src)["0分"]["P6"].value == 13, "the source workbook is never written"
    assert not list(tmp_path.glob(".neon-export*")), "no temp file left beside the export"


def test_nothing_is_published_when_the_file_does_not_match_the_book(src, tmp_path, monkeypatch):
    out = tmp_path / "Neon分v14-export.xlsx"
    out.write_text("yesterday")
    monkeypatch.setattr(ex, "apply_changes", lambda xlsx, changes, built: __import__("shutil").copyfile(xlsx, built))
    dbp = tmp_path / "neon.db"
    db = ledger.connect(dbp)
    _w(db, "2026-11-22T09:00:00", "0分", "P", 6, "=13+20")
    db.commit()
    db.close()
    res = ex.export(src, out, db_path=dbp)
    assert not res["published"] and res["differ"][0]["cell"] == "0分!P6"
    assert out.read_text() == "yesterday", "the previous export stays"


def test_a_ledger_that_cannot_be_read_is_not_exported_as_zero_writes(src, tmp_path, monkeypatch):
    """book.writes_since answers [] on a sqlite error; the export counts the
    journal itself and refuses when the book replayed fewer writes."""
    monkeypatch.setattr(ex, "journaled_since", lambda since, db_path=None: 4)
    res = ex.export(src, tmp_path / "o.xlsx", db_path=tmp_path / "absent.db")
    assert not res["published"] and "replayed 0" in res["error"]


def test_the_export_name_is_one_no_neon_picker_matches():
    """Excel's audit macro (IsNeon: no hyphen) and every Neon分v[\\d.]+\\.xlsx$
    regex take a bare Neon分v14.xlsx for the live workbook."""
    assert "-" in ex.OUT.name and ex.OUT.parent == book.SRC.parent
    assert not re.match(r"Neon分v[\d.]+\.xlsx$", ex.OUT.name)
    assert ex.main(["--src", str(book.SRC), "--out", str(book.SRC)]) == 1, "never over the source"
