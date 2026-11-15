"""2n1: the 1₦ efficiency rule, month selection, mark gating, the shared 2₦ writer (no Excel, no network)."""
import importlib.util
import sys
import types
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("n1_2", HERE / "2n1.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["n1_2"] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load()


def _sheet():
    """A..H: A tag, B week, C/D fixed habits, E rate habit, F blank column, G ∑, H avg."""
    return [
        ["", "5.0", "1s", "1 i9", "family", "", "∑", "avg"],
        ["", "()", "20.0", "10.0", "120.0", "", "150.0", ""],
        ["", "Day of Week ", "1.0", "2.0", "7.0", "", "", ""],
        ["", "[] Target", "45.0", "40.0", "120.0", "", "205.0", ""],
        ["", "[]", "45.0", "40.0", "1/m", "", "", ""],
        ["sf21", "10.1", "1.0", "", "121.0", "", "9.0", "0.65"],
        ["sf21", "10.2", "7.0", "1.0", "", "9.0", "9.0", ""],
        ["sf21", "10.3", "0.0", "1.0", "60.0", "", "9.0", ""],
        ["sf21", "10.4", "1.0", "", "120.0", "", "9.0", ""],
        ["sf21", "11.1", "1.0", "1.0", "", "", "9.0", ""],
    ]


def test_month_efficiency_matches_the_sheet_formula():
    e = m.month_efficiency(m.parse_sheet(_sheet()), 10)
    # 1s: done 3 of 4 (an explicit 0 is not done) → 135; 1 i9: 2 of 4 → 80; family (rate): 301
    assert e["weeks"] == 4 and e["earned"] == 135 + 80 + 301 and e["possible"] == 4 * 205
    assert abs(e["pct"] - 516 / 820) < 1e-9
    by = {h["habit"]: h for h in e["habits"]}
    assert by["1s"]["done"] == 3 and by["1s"]["missed"] == 45
    assert by["1 i9"]["missed"] == 80 and by["family"]["missed"] == 4 * 120 - 301
    assert "" not in by                                   # the blank-rate column is not a habit
    assert e["habits"][0]["habit"] == "family"            # sorted by points missed


def test_sheet_avg_blank_or_mismatched_is_flagged():
    e = m.month_efficiency(m.parse_sheet(_sheet()), 10)
    assert e["sheet_avg"] == 0.65 and not e["avg_ok"] and "WARNING" in m.render(e)
    e11 = m.month_efficiency(m.parse_sheet(_sheet()), 11)
    assert e11["sheet_avg"] is None and "blank" in m.render(e11)
    s = _sheet(); s[5][7] = str(516 / 820)
    ok = m.month_efficiency(m.parse_sheet(s), 10)
    assert ok["avg_ok"] and "WARNING" not in m.render(ok)


def test_month_parsing_and_default():
    assert [m.parse_month(x) for x in ("10", "oct", "October", "2026-10")] == [10] * 4
    assert m.default_month(date(2026, 11, 14)) == 10 and m.default_month(date(2027, 1, 3)) == 12


def test_card_key_ignores_estimates():
    assert m._card_key("5^2 1 n Efficiency Clearance (5) [25]") == m.CARD_NAME.lower()


def test_mark_only_a_due_card_and_credit_its_due_month():
    today = date(2026, 11, 14)
    assert m.mark_plan(None, today)[0] is False
    assert m.mark_plan({"due": {"date": "2026-11-11"}}, today)[:2] == (True, 11)
    assert m.mark_plan({"due": {"date": "2026-10-11"}}, today)[:2] == (True, 10)   # late close → October
    assert m.mark_plan({"due": {"date": "2026-12-11"}}, today)[0] is False         # already advanced


def _fake_block(writes):
    rows = [[""] * 16 for _ in range(30)]                                  # A61:P90
    rows[0] = ["", "", "", "₹"] + [f"{i}.0" for i in range(1, 13)]
    rows[1] = ["1.0", "5^2 org", "", "60.0"] + [""] * 12                   # due day 1.0 must not match month 1
    rows[13] = ["15.0", "1 ₦ efficiency", "", "25.0", "25.0"] + [""] * 11  # row 74
    fake = types.SimpleNamespace(
        read_range=lambda sheet, rng: {"ok": True, "row": 61, "rows": rows},
        batch_write=lambda sheet, w, **kw: writes.append((w, kw)) or {"ok": True})
    return rows, fake


def test_shared_writer_finds_october_and_is_idempotent(monkeypatch):
    """2026-11-14: the /2hci copy's rstrip('.0') read '10.0' as month 1."""
    writes = []
    rows, fake = _fake_block(writes)
    monkeypatch.setitem(sys.modules, "neon", types.SimpleNamespace(excel=fake))
    monkeypatch.setitem(sys.modules, "neon.excel", fake)
    r = m._monthly.credit_monthly_cell(m.MONTHLY_HABIT, 10)
    assert r["ok"] and (r["row"], r["col"], r["value"]) == (74, "N", 25)
    r1 = m._monthly.credit_monthly_cell(m.MONTHLY_HABIT, 1)
    assert r1.get("skipped") and r1["col"] == "E" and len(writes) == 1
    assert not m._monthly.credit_monthly_cell("no such habit", 10)["ok"]
