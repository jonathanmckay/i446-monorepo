"""1g_set.py: section bounds, formula-safe clears, daemon writes."""
import importlib.util
import sys
from pathlib import Path

import pytest

_P = Path(__file__).parent / "1g_set.py"
_S = importlib.util.spec_from_file_location("set1g", _P)
s = importlib.util.module_from_spec(_S)
sys.modules["set1g"] = s
_S.loader.exec_module(s)

US, RS = "\x1f", "\x1e"


def _layout(rows: dict) -> dict:
    """rows: {r: (A, D, E, F, G)} → a scan blob → parse_scan (round trip)."""
    lines = []
    for r in range(1, s.SCAN_ROWS + 1):
        vals = rows.get(r, ("", "", "", "", ""))
        lines.append(US.join(vals) + US)
    return s.parse_scan(RS.join(lines) + RS)


# Live shape on 2026-10-30: " xk87" with a leading space; s897 is the last
# section and row 46 carries the totals formulas.
LIVE = _layout({
    34: (" xk87", "T", "", "", ""),
    35: ("", "Plan next Spokane outing", "20", "40", "0"),
    38: ("hcmc", "Th", "", "", ""),
    39: ("", "No late night youtube ", "0", "300", "0"),
    41: ("s897", "", "", "", ""),
    42: ("", "Meet with Kelly / family", "20", "40", "0"),
    43: ("", "Coordinate fort dates", "15", "30", ""),
    46: ("", "", "", "=SUM(W5:W9,F21:F22,F35:F36,F39)", "=I46/F46"),
    48: ("", "", "", "", "1"),
})


def test_leading_space_header_matches():
    assert s.section_bounds(LIVE, "xk87") == (34, 38)


def test_last_section_stops_at_totals_row():
    assert s.section_bounds(LIVE, "s897") == (41, 46)
    p = s.plan(LIVE, "s897", [{"text": "Dinner", "fen": 20, "focus": 40}])
    assert set(p["rows"]) == {42, 43}           # 46 and 48 never touched
    assert [it["col"] for it in p["rows"][43]] == ["D", "E", "F"]   # only non-empty cells
    assert all(it["value"] == "" and it["mode"] == "text" for it in p["rows"][43])


def test_goal_writes_and_numbers():
    p = s.plan(LIVE, "xk87", [{"text": " Plan outing ", "fen": "20", "focus": 40.0}])
    assert p["rows"][35] == [{"col": "D", "value": "Plan outing", "mode": "text"},
                             {"col": "E", "value": 20}, {"col": "F", "value": 40}]


def test_too_many_goals_refused():
    with pytest.raises(ValueError, match="only 3 rows"):
        s.plan(LIVE, "xk87", [{"text": t} for t in "abcd"])


def test_missing_section():
    with pytest.raises(LookupError):
        s.plan(LIVE, "zz99", [{"text": "x"}])


def test_m5x2_alias():
    lay = _layout({13: ("m5c7", "M", "", "150", ""), 17: ("hcmp", "", "", "", "")})
    assert s.plan(lay, "m5x2", [{"text": "x"}])["header"] == 13


def test_apply_one_batch_write_per_row(monkeypatch):
    calls = []
    monkeypatch.setattr(s.neon_excel, "batch_write",
                        lambda sheet, items, *, row=None, date=None, src=None, fallback=True:
                        calls.append((sheet, row, src, fallback)) or {"ok": True})
    p = s.plan(LIVE, "s897", [{"text": "Dinner", "fen": 20, "focus": 40}])
    assert s.apply(p, "s897") == []
    assert calls == [("1g", 42, "1g s897", True), ("1g", 43, "1g s897", True)]
