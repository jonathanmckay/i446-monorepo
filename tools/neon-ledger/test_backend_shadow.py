"""backend_shadow: the journal judged against the book."""
import datetime as dt
import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import backend_shadow as bs  # noqa: E402
from neon import book, calc  # noqa: E402
from neon.calc import col_index as ci  # noqa: E402

SAVED = "2026-11-22T07:50:45"


def _setup(tmp_path, lines):
    cells = {("0分", ci("B"), 324): calc.serial(dt.date(2026, 11, 22)), ("0分", ci("P"), 324): "=13",
             ("0分", ci("D"), 324): "=SUM(P324:Y324)", ("0n", ci("C"), 327): calc.serial(dt.date(2026, 11, 22))}
    seed = tmp_path / "seed.pickle"
    seed.write_bytes(pickle.dumps({"meta": {"version": book.SEED_VERSION, "saved": SAVED, "src_mtime": 0},
                                   "cells": cells, "arrays": {}, "cached": {}}))
    jd = tmp_path / "journal"
    jd.mkdir()
    (jd / "2026-11.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
    bs.init(seed, tmp_path / "state.pickle")
    return jd


def _w(ts, col, value, before, after, bv, av, kind="write", sheet="0分", row=324, **kw):
    e = {"ts": ts, "kind": kind, "sheet": sheet, "col": col, "row": row, "date": "11/22", "value": value,
         "before": before, "after": after, "before_value": bv, "after_value": av, "src": "t", "chain": "ok"}
    e.update(kw)
    return e


def _run(tmp_path, jd):
    return bs.run(tmp_path / "state.pickle", jd, tmp_path / "log.jsonl", today=dt.date(2026, 11, 22))


def test_clean_writes_and_incremental_runs(tmp_path):
    jd = _setup(tmp_path, [
        _w("2026-11-22T07:40:00", "P", "=99", "", "=99", "", "99.0"),                 # before the margin: in the seed
        _w("2026-11-22T07:49:00", "P", "=13", "=99", "=13", "99.0", "13.0"),          # inside it: adopted, not judged
        _w("2026-11-22T07:52:00", "P", "=13+4", "=13", "=13+4", "13.0", "17.0"),
        _w("2026-11-22T07:53:00", "Q", "+10", "", "=10", "", "10.0", kind="append"),
    ])
    r = _run(tmp_path, jd)
    assert (r["judged"], r["clean"], r["adopted"]) == (2, 2, 1) and r["misses"] == []
    assert _run(tmp_path, jd)["judged"] == 0                                           # nothing new
    with open(jd / "2026-11.jsonl", "a") as f:
        f.write(json.dumps(_w("2026-11-22T07:54:00", "P", "=13+7", "=13+4", "=13+7", "17.0", "20.0")) + "\n")
        f.write('{"ts": "2026-11-22T07:55:00", "kind": "wri')                         # a torn line waits
    r = _run(tmp_path, jd)
    assert (r["judged"], r["clean"]) == (1, 1)


def test_causes_are_told_apart_and_truth_is_adopted(tmp_path):
    jd = _setup(tmp_path, [
        _w("2026-11-22T08:00:00", "P", "=13+4", "=13+1", "=13+4", "14.0", "17.0"),     # someone typed =13+1 first
        _w("2026-11-22T08:01:00", "P", "=13+7", "=13+4", "=13+7", "17.0", "20.0"),     # clean again: truth adopted
        _w("2026-11-22T08:02:00", "Q", "+5", "", "=5+0", "", "5.0", kind="append"),    # Excel "did" something else
        _w("2026-11-22T08:03:00", "R", "=40", "", "=40", "", "41.0"),                  # value Excel shows differs
        _w("2026-11-22T08:04:00", "S", "=18", "", "=18", "", "18", row=325),           # the date is row 324
        _w("2026-11-22T08:05:00", "T", "=56", "", "=56", "", "56"),                    # same number, other text
        _w("2026-11-22T08:06:00", "U", "+1", "", "", "", "", kind="append"),           # empty read-back
        {"ts": "2026-11-22T08:07:00", "kind": "snapshot", "sheet": "0分", "col": "V", "row": 324, "after": "=7"},
    ])
    r = _run(tmp_path, jd)
    causes = [(m["cell"], m["cause"]) for m in r["misses"]]
    assert causes == [("0分!P324", "drift"), ("0分!Q324", "backend"), ("0分!R324", "value"),
                      ("0分!S325", "lookup"), ("0分!T324", "format")]
    assert (r["judged"], r["clean"], r["blind"], r["adopted"]) == (6, 1, 1, 1)
    st = pickle.loads((tmp_path / "state.pickle").read_bytes())
    assert st["cells"][("0分", ci("Q"), 324)] == "=5+0" and st["cells"][("0分", ci("V"), 324)] == "=7"
    assert st["cells"][("0分", ci("U"), 324)] == "=1"                                 # blind: the book's own result
    lines = [json.loads(x) for x in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert lines[-1]["run"] is True and lines[-1]["backend"] == 1 and len(lines) == 6
