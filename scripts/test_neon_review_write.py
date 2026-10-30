"""neon-review-write.py: row checks before a journaled /batch_write."""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture()
def w(monkeypatch):
    spec = importlib.util.spec_from_file_location("neon_review_write", HERE / "neon-review-write.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["neon_review_write"] = mod
    spec.loader.exec_module(mod)
    calls = []

    def fake_bw(sheet, items, *, date=None, row=None, src=None, fallback=True):
        calls.append(dict(sheet=sheet, items=list(items), date=date, row=row, src=src,
                          fallback=fallback))
        return {"ok": True, "row": row, "results": [{"col": i["col"], "chain": "ok"} for i in items]}
    monkeypatch.setattr(mod.excel, "batch_write", fake_bw)
    monkeypatch.setattr(mod.excel, "read",
                        lambda sheet, col, row=None, date=None: {"ok": True, "value": "10.3"})
    monkeypatch.setattr(mod.excel, "lookup_row", lambda sheet, key: 295)
    mod._calls = calls
    return mod


def test_parse_cell(w, tmp_path):
    f = tmp_path / "t.txt"
    f.write_text('line one\nline "two"\n', encoding="utf-8")
    assert w.parse_cell(f"D=@{f}") == {"col": "D", "value": 'line one\nline "two"', "mode": "text"}
    assert w.parse_cell("F=2134") == {"col": "F", "value": 2134}
    assert w.parse_cell("f=1.5") == {"col": "F", "value": 1.5}
    assert w.parse_cell("B=MA") == {"col": "B", "value": "MA", "mode": "text"}
    assert w.parse_cell("B==SUM(A1)") == {"col": "B", "value": "=SUM(A1)", "mode": "text"}
    with pytest.raises(ValueError):
        w.parse_cell("nocol")


def test_label_row_write(w):
    assert w.main(["--sheet", "i9+m5x2", "--row", "44", "--label", "10.3", "--src", "1i9",
                   "B=MA", "F=10"]) == 0
    (c,) = w._calls
    assert c["row"] == 44 and c["date"] == "10.3" and c["src"] == "1i9" and c["fallback"] is True
    assert c["items"] == [{"col": "B", "value": "MA", "mode": "text"}, {"col": "F", "value": 10}]


def test_label_mismatch_writes_nothing(w):
    assert w.main(["--sheet", "i9+m5x2", "--row", "45", "--label", "10.4", "--src", "1i9",
                   "B=MA"]) == 1
    assert w._calls == []


def test_same_label_tolerates_float_noise(w):
    assert w.same_label("10.299999999999", "10.3")
    assert not w.same_label("10.2", "10.3")


def test_date_write_with_merge_check(w, monkeypatch):
    monkeypatch.setattr(w, "merge_top", lambda sheet, col, row: 295)
    assert w.main(["--sheet", "0s897", "--date", "2026-10-21", "--merge-col", "Z",
                   "--src", "1s897", "Z=340"]) == 0
    (c,) = w._calls
    assert c["row"] == 295 and c["date"] == "10/21/26"


def test_merge_mismatch_writes_nothing(w, monkeypatch):
    monkeypatch.setattr(w, "merge_top", lambda sheet, col, row: 294)
    assert w.main(["--sheet", "0s897", "--date", "2026-10-21", "--merge-col", "Z",
                   "--src", "1s897", "Z=340"]) == 1
    assert w._calls == []


def test_chunks_and_refuses_fallback_for_formula_like_prose(w):
    cells = ["B=a", "D=b", "E=c", "F=1", "G==oops"]
    assert w.main(["--sheet", "i9+m5x2", "--row", "44", "--src", "x", *cells]) == 0
    assert [len(c["items"]) for c in w._calls] == [4, 1]
    assert all(c["fallback"] is False for c in w._calls)
