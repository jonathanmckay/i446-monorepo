"""tools/neon-ledger/shadow_apply.py — pure comparison of ledger-derived vs live Excel."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
spec = importlib.util.spec_from_file_location("shadow_apply", HERE / "shadow_apply.py")
sa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sa)


def _cols(**kw):
    base = {c: {"derived": 0.0, "unknown": []} for c in sa.rules.POINT_COLS + ["D"]}
    for k, v in kw.items():
        base[k] = v
    return base


def test_compare_judges_only_known_columns():
    derived = _cols(R={"derived": 403.0, "unknown": []},
                    W={"derived": 156.929, "unknown": []},
                    X={"derived": 50.0, "unknown": ["'1n+'!AF5"]},
                    S={"derived": None, "unknown": []},
                    D={"derived": 610.0, "unknown": ["'1n+'!AF5"]})
    excel = {c: 0.0 for c in sa.rules.POINT_COLS + ["D"]}
    excel.update(R=403.0, W=156.928571428571, X=58.0, D=617.93, S=None)
    recs = {r["col"]: r for r in sa.compare(derived, excel)}
    assert recs["R"]["judged"] and recs["R"]["agree"] and recs["R"]["delta"] == 0.0
    assert recs["W"]["agree"]                                  # within TOL
    assert not recs["X"]["judged"] and recs["X"]["agree"] is None and recs["X"]["delta"] == -8.0
    assert not recs["S"]["judged"] and recs["S"]["would_write"] is None
    assert not recs["D"]["judged"]                             # unknown input propagates to Σ


def test_compare_flags_a_real_disagreement():
    derived = _cols(T={"derived": 240.0, "unknown": []})
    excel = {c: 0.0 for c in sa.rules.POINT_COLS + ["D"]}
    excel["T"] = 285.0
    recs = {r["col"]: r for r in sa.compare(derived, excel)}
    assert recs["T"]["judged"] and recs["T"]["agree"] is False and recs["T"]["delta"] == -45.0


def test_no_baseline_judges_and_writes_nothing():
    """2026-11-19: the 03:50 shadow run raced the 03:50 reconcile every day and
    judged ledger 0 against Excel's formula floor (个 4, hcb -50); a block-job
    apply would have written those zeros. Without the baseline nothing is
    judged and would_write is null, even when Excel and the ledger differ."""
    excel = {c: 0.0 for c in sa.rules.POINT_COLS + ["D"]}
    excel.update(T=4.0, W=-50.0, D=-46.0)
    recs = sa.unbaselined(sa.compare(_cols(), excel))
    assert all(r["judged"] is False and r["agree"] is None and r["would_write"] is None for r in recs)
    assert {r["col"]: r["delta"] for r in recs}["W"] == 50.0      # the gap stays visible in the log


def test_run_without_baseline_is_not_clean_and_counts_it(tmp_path, monkeypatch):
    """The real night: a 0n set exists at 00:15 (allcolors handicap), so "any
    event" is true, but the 0分 baseline has not landed."""
    db = tmp_path / "neon.db"
    c = sa.ledger.connect(db)
    sa.ledger.record({"ts": "2026-11-19T00:15:09", "kind": "write", "sheet": "0n", "col": "AH", "row": 324,
                      "date": "11/19", "value": "1", "before": "", "after": "1", "after_value": "1.0",
                      "before_value": "", "src": "allcolors auto: handicap", "chain": "ok"}, c)
    assert c.execute("SELECT COUNT(*) FROM events WHERE date='2026-11-19'").fetchone()[0] == 1
    assert sa.ledger.has_baseline("2026-11-19", c) is False
    c.commit()
    c.close()
    monkeypatch.setattr(sa.ledger, "catch_up", lambda *a, **k: None)
    monkeypatch.setattr(sa.ledger, "connect", lambda *a, **k: __import__("sqlite3").connect(db))
    monkeypatch.setattr(sa.rules, "derive_day", lambda d, c=None, strict=False: {"cols": _cols(), "blocks": {}})
    excel = {c: 0.0 for c in sa.rules.POINT_COLS + ["D"]}
    excel.update(T=4.0, W=-50.0, D=-46.0)
    monkeypatch.setattr(sa, "read_excel_row", lambda d: (321, excel))
    monkeypatch.setattr(sa, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(sa, "SUMMARY", tmp_path / "out" / "shadow-summary.json")
    b = sa.run("2026-11-19", 4)
    assert b["no_baseline"] is True and b["clean"] is False and b["disagree"] == []
    import json
    s = json.loads((tmp_path / "out" / "shadow-summary.json").read_text())
    assert s["days"]["2026-11-19"]["no_baseline"] == 1 and s["days"]["2026-11-19"]["clean"] == 0

    # once the reconcile has observed the row, the same run judges again
    c = sa.ledger.connect(db)
    sa.ledger.record({"ts": "2026-11-19T03:50:15", "kind": "baseline", "sheet": "0分", "col": "T", "row": 321,
                      "date": "11/19", "value": "4", "after": "=4", "after_value": "4.0", "src": "reconcile"}, c)
    assert sa.ledger.has_baseline("2026-11-19", c) is True
    c.commit()
    c.close()
    b = sa.run("2026-11-19", 4)
    assert b["no_baseline"] is False and b["disagree"] == ["D", "T", "W"]


def test_compare_with_no_excel_row_judges_nothing():
    recs = sa.compare(_cols(), {})
    assert all(r["judged"] is False for r in recs)
