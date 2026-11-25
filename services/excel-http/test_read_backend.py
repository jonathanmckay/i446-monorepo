"""Reads answered from the book and the daemon's shadow comparison (2026-11-25)."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "lib")))

from neon import calc  # noqa: E402
import read_backend as rb  # noqa: E402

D = calc.serial(dt.date(2026, 11, 25))


def book():
    b = calc.Book({
        ("0分", 2, 326): D - 1, ("0分", 2, 327): D,
        ("0分", 4, 327): "=SUM(P327:Y327)", ("0分", 16, 327): "=4+10", ("0分", 17, 327): 3.5,
        ("1n+", 2, 50): 11.3, ("1n+", 3, 50): 20.0,
    })
    b.dates = {("0分", 2, 326), ("0分", 2, 327)}
    return b


def test_read_by_date_and_row():
    r = rb.answer("/read", {"sheet": "0分", "col": "D", "date": "11/25"}, book())
    assert r == {"ok": True, "row": 327, "col": "D", "value": "17.5", "formula": "=SUM(P327:Y327)"}
    r = rb.answer("/read", {"sheet": "0分", "col": "P", "row": 327}, book())
    assert (r["value"], r["formula"]) == ("14.0", "=4+10")
    assert rb.answer("/read", {"sheet": "0分", "col": "D", "date": "1/1"}, book()) == {
        "ok": False, "error": "date_not_found_or_missing_target"}


def test_lookup_and_week_label():
    assert rb.answer("/lookup", {"sheet": "0分", "date": "2026-11-25"}, book()) == {"ok": True, "row": 327}
    assert rb.answer("/lookup", {"sheet": "1n+", "date": "11.3"}, book()) == {"ok": True, "row": 50}
    assert rb.answer("/lookup", {"sheet": "0分", "date": "12/31"}, book()) == {"ok": False, "error": "date_not_found"}
    assert rb.answer("/lookup", {"sheet": "0分"}, book())["error"] == "missing_sheet_or_date"


def test_read_range_reply_and_validation():
    r = rb.answer("/read_range", {"sheet": "0分", "range": "b326:d327"}, book())
    assert r["ok"] and r["range"] == "B326:D327" and r["row"] == 326
    assert r["rows"][1] == ["Wednesday, November 25, 2026 at 12:00:00 AM", "", "17.5"]
    assert rb.answer("/read_range", {"sheet": "0分", "range": "D5"}, book())["error"] == "missing_sheet_or_bad_range"
    assert rb.answer("/read_range", {"sheet": "0分", "range": "D5:B2"}, book())["error"] == "bad_range"
    assert rb.answer("/read_range", {"sheet": "0分", "range": "A1:ZZ900"}, book())["error"] == "range_too_large"


def test_compare_causes():
    b = book()
    mine = rb.answer("/read", {"sheet": "0分", "col": "P", "row": 327}, b)
    ok = {"ok": True, "row": 327, "col": "P", "value": "14.0", "formula": "=4+10"}
    assert rb.compare("/read", ok, mine) is None
    assert rb.compare("/read", {**ok, "formula": "=4+10+3", "value": "17.0"}, mine)["cause"] == "drift"
    assert rb.compare("/read", {**ok, "value": "15.0"}, mine)["cause"] == "value"
    assert rb.compare("/read", {**ok, "value": "14"}, mine)["cause"] == "format"
    assert rb.compare("/read", {**ok, "row": 328}, mine)["cause"] == "lookup"
    assert rb.compare("/lookup", {"ok": True, "row": 326}, {"ok": True, "row": 327})["cause"] == "lookup"
    assert rb.compare("/read", ok, {"ok": False, "error": "no_book"}) == {"cause": "nobook"}
    rng = rb.answer("/read_range", {"sheet": "0分", "range": "P327:Q327"}, b)
    assert rb.compare("/read_range", {"ok": True, "rows": [["14.0", "3.5"]]}, rng) is None
    d = rb.compare("/read_range", {"ok": True, "rows": [["14.0", "4.0"]]}, rng)
    assert d["cause"] == "range" and d["cells"] == 1 and d["examples"][0]["cell"] == "Q327"
    assert rb.compare("/read_range", {"ok": True, "rows": [["14.0"]]}, rng)["what"] == "shape"


def test_shadow_counts_dedupes_and_never_raises(tmp_path):
    log, counts = tmp_path / "s.jsonl", tmp_path / "c.json"
    sh = rb.Shadow(str(log), str(counts), load=book)
    req = {"sheet": "0分", "col": "P", "row": 327}
    assert sh.judge("/read", req, {"ok": True, "row": 327, "col": "P", "value": "14.0", "formula": "=4+10"}) is None
    bad = {"ok": True, "row": 327, "col": "P", "value": "15.0", "formula": "=4+10"}
    assert sh.judge("/read", req, bad)["cause"] == "value"
    assert sh.judge("/read", req, bad)["cause"] == "value"            # the 45 s poller again
    assert sh.judge("/read", req, {"ok": False, "error": "x"}) is None  # failed Excel reads are not judged
    assert sh.judge("/write", req, bad) is None
    today = sh.state()["today"]
    assert (today["judged"], today["clean"], today["value"]) == (3, 1, 2)
    assert len(log.read_text().splitlines()) == 1
    assert json.loads(counts.read_text())

    def boom():
        raise RuntimeError("seed unreadable")
    sh2 = rb.Shadow(str(log), str(tmp_path / "c2.json"), load=boom)
    assert sh2.judge("/read", req, bad)["cause"] == "error"
    assert rb.Shadow(str(log), str(tmp_path / "c3.json"), load=lambda: None).judge("/read", req, bad) == {"cause": "nobook"}
