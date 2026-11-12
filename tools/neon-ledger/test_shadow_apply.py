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


def test_compare_with_no_excel_row_judges_nothing():
    recs = sa.compare(_cols(), {})
    assert all(r["judged"] is False for r in recs)
