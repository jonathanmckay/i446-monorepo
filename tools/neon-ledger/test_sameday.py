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


def test_hand_typed_0n_cell_points_to_did_with_a_safe_alias(monkeypatch):
    # 11/3's actual miss: the raw header "NVC + e" would read as a +bonus in did-fast
    monkeypatch.setattr(sd, "did_name", lambda cell, hdr: "nvc")
    out = sd.fixes([_m("X", "0n!AO(NVC\xa0+ e): Excel 1, ledger — (typed into 0n?)")])
    assert out == ["0n!AO (NVC + e) = 1 typed in Excel → `/did nvc 1`"]


def test_typed_fen_term_says_delete_then_log_not_log_on_top():
    out = sd.fixes([_m("R", "+60 in Excel formula, never journaled (typed into Excel?)")])
    assert out == ["0分!R has +60 typed into Excel → delete it from the cell, then "
                   "`+60 @i9 <what it was for>`"]


def test_hcbi_set_skills_rerun_but_ate_cells_clear_first():
    out = sd.fixes([_m("W",
                       "hcbi!AI302(AI): Excel 10, ledger blank (typed into hcbi?)",
                       "hcbi!H302(cr): Excel 3, ledger 1 (typed into hcbi?)")])
    assert out[0] == "hcbi!AI (AI) = 10 typed in Excel → `/out 10`"
    assert "re-log it with `/ate`" in out[1] and "don't log on top" in out[1]


def test_untraced_miss_still_reported():
    assert "no traced cause" in sd.fixes([{"col": "U", "field": "媒", "delta": 1.19, "why": []}])[0]


def test_did_name_prefers_the_shortest_alias(tmp_path, monkeypatch):
    h = tmp_path / "headers.json"
    h.write_text('{"0n": {"nvc + e": 41, "nvc": 41, "o314": 43}}')
    monkeypatch.setattr(sd, "HEADERS", h)
    assert sd.did_name("AO", "NVC + e") == "nvc"
    assert sd.did_name("ZZ", "mystery") == "mystery"
