"""tools/neon-ledger/sameday.py — the 22:30 same-day ledger drift card."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("sameday", Path(__file__).parent / "sameday.py")
sd = importlib.util.module_from_spec(_SPEC)
sys.modules["sameday"] = sd
_SPEC.loader.exec_module(sd)


def _m(col, *why, field="x", delta=1.0):
    return {"col": col, "field": field, "delta": delta, "why": list(why)}


def test_hand_typed_0n_cell_is_adopted_not_redone_with_did():
    # 11/3's actual miss. /did would add for cumulative habits and close the card.
    out = sd.fixes([_m("X", "0n!AO(NVC\xa0+ e): Excel 1, ledger — (typed into 0n?)")])
    assert out == ["0n!AO (NVC + e) = 1 typed in Excel → `sameday.py adopt 0n!AO 1`"]


def test_typed_fen_term_says_delete_then_log_not_log_on_top():
    out = sd.fixes([_m("R", "+60 in Excel formula, never journaled (typed into Excel?)")])
    assert out == ["0分!R has +60 typed into Excel → delete it from the cell, then "
                   "`+60 @i9 <what it was for>`"]


def test_hcbi_cells_are_adopted_as_is():
    out = sd.fixes([_m("W",
                       "hcbi!AI302(AI): Excel 10, ledger blank (typed into hcbi?)",
                       "hcbi!H302(cr): Excel 3, ledger 1 (typed into hcbi?)")])
    assert out == ["hcbi!AI (AI) = 10 typed in Excel → `sameday.py adopt hcbi!AI 10`",
                   "hcbi!H (cr) = 3 typed in Excel → `sameday.py adopt hcbi!H 3`"]


def test_adopt_refuses_fen_terms():
    import datetime as dt
    import pytest
    with pytest.raises(SystemExit, match="分 log"):
        sd.adopt("0分!R", "60", dt.date(2026, 11, 3))


def test_adopt_sets_same_value_and_acks(monkeypatch):
    import datetime as dt
    from neon import excel
    calls, runs = [], []
    monkeypatch.setattr(excel, "batch_write", lambda sh, w, **k: calls.append((sh, w, k)) or {"ok": True})
    monkeypatch.setattr(sd.subprocess, "run", lambda cmd, **k: runs.append(cmd))
    assert sd.adopt("0n!AO", "1", dt.date(2026, 11, 3)) == 0
    assert calls == [("0n", [{"col": "AO", "value": 1, "mode": "set"}],
                      {"date": "11/3", "src": "adopt hand edit 0n!AO"})]
    assert runs and "--ack" in runs[0] and "0n!AO" in runs[0]


def test_untraced_miss_still_reported():
    assert "no traced cause" in sd.fixes([{"col": "U", "field": "媒", "delta": 1.19, "why": []}])[0]
