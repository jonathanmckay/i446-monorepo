"""-2n's Neon reads go through the excel-http client, not AppleScript.

2026-11-25: check_neon_column, last_meal_today, _unfinished_0n_today and
_open_weekly_1g each ran their own AppleScript against Excel (three of them
against whatever Excel was open on the machine running the TUI). The daemon is
about to answer from its own model, so a raw read would see a different book.
These tests pin the daemon-backed versions against a faked client: same parsed
results as the AppleScript returned, same quiet failure (None / []).
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("_two_n_reads", HERE / "-2n.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


m = _load()
TODAY = f"{datetime.now().month}/{datetime.now().day}"


def _num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


class FakeExcel:
    """Stands in for lib/neon/excel.py. cells = {sheet: {"AK331": "text"}},
    rows = {(sheet, "M/D"): row}. Replies have the daemon's shapes."""

    def __init__(self, cells=None, rows=None, fail=()):
        self.cells = cells or {}
        self.rows = rows or {}
        self.fail = set(fail)
        self.calls = []

    def lookup_row(self, sheet, date_str):
        self.calls.append(("lookup_row", sheet, date_str))
        if "lookup_row" in self.fail:
            return None
        return self.rows.get((sheet, date_str))

    def read_range(self, sheet, rng):
        self.calls.append(("read_range", sheet, rng))
        if "read_range" in self.fail:
            return {"ok": False, "error": "excel-http unreachable"}
        c1, r1, c2, r2 = re.match(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$", rng).groups()
        sh = self.cells.get(sheet, {})
        out = []
        for r in range(int(r1), int(r2) + 1):
            out.append([sh.get(f"{m._col_letter(c)}{r}", "")
                        for c in range(_num(c1), _num(c2) + 1)])
        return {"ok": True, "range": rng, "row": int(r1), "rows": out}

    def __getattr__(self, name):   # read/write/append/...: none may be used
        raise AssertionError(f"unexpected excel client call: {name}")


def _use(monkeypatch, fake):
    monkeypatch.setattr(m, "_neon_excel", lambda: fake)
    return fake


def _boom():
    raise RuntimeError("no client")


# ── column helpers ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("n,letters", [(1, "A"), (26, "Z"), (27, "AA"), (37, "AK"),
                                       (42, "AP"), (55, "BC"), (60, "BH")])
def test_col_letter_and_num_round_trip(n, letters):
    assert m._col_letter(n) == letters
    assert m._col_num(letters) == n


# ── check_neon_column ────────────────────────────────────────────────────────

def _0n(value, header="ص", col="AP", row=330):
    return FakeExcel(cells={"0n": {f"{col}1": header, "F1": "wake up ", f"{col}{row}": value,
                                   "F%d" % row: "1.0"}},
                     rows={("0n", TODAY): row})


def test_check_neon_column_returns_daemon_text(monkeypatch):
    fake = _use(monkeypatch, _0n("3.0"))
    assert m.check_neon_column("ص") == "3.0"
    assert ("lookup_row", "0n", TODAY) in fake.calls
    assert ("read_range", "0n", "AP330:AP330") in fake.calls


@pytest.mark.parametrize("blank", ["", "0", "0.0", "  "])
def test_check_neon_column_blank_or_zero_is_none(monkeypatch, blank):
    _use(monkeypatch, _0n(blank))
    assert m.check_neon_column("ص") is None


def test_check_neon_column_ignores_trailing_space_in_header(monkeypatch):
    # The live row 1 has "wake up " with a trailing space; the AppleScript
    # stripped trailing whitespace before comparing.
    _use(monkeypatch, _0n("3.0"))
    assert m.check_neon_column("wake up") == "1.0"


def test_check_neon_column_unknown_header_is_none(monkeypatch):
    fake = _use(monkeypatch, _0n("3.0"))
    assert m.check_neon_column("no such habit") is None
    assert not [c for c in fake.calls if c[0] == "lookup_row"]


def test_check_neon_column_no_row_for_today_is_none(monkeypatch):
    fake = _0n("3.0")
    fake.rows = {}
    _use(monkeypatch, fake)
    assert m.check_neon_column("ص") is None


@pytest.mark.parametrize("fail", ["read_range", "lookup_row"])
def test_check_neon_column_daemon_failure_is_none(monkeypatch, fail):
    fake = _0n("3.0")
    fake.fail = {fail}
    _use(monkeypatch, fake)
    assert m.check_neon_column("ص") is None


def test_check_neon_column_client_missing_is_none(monkeypatch):
    monkeypatch.setattr(m, "_neon_excel", _boom)
    assert m.check_neon_column("ص") is None


# ── _unfinished_0n_today ─────────────────────────────────────────────────────

def _headers(tmp_path, monkeypatch, hdrs):
    d = tmp_path / ".claude/skills/did"
    d.mkdir(parents=True)
    (d / "headers.json").write_text(json.dumps({"0n": hdrs}, ensure_ascii=False))
    monkeypatch.setattr(m.Path, "home", classmethod(lambda cls: tmp_path))


def test_unfinished_lists_blank_habits_in_header_order(tmp_path, monkeypatch):
    _headers(tmp_path, monkeypatch, {"日": 2, "2026.0": 3, "睡觉": 4, "cpap": 5,
                                    "0l": 19, "n color": 32, "ص": 42})
    fake = _use(monkeypatch, FakeExcel(
        cells={"0n": {"B330": "W", "D330": "449.0", "E330": "0.0", "AF330": ""}},
        rows={("0n", TODAY): 330}))
    # 睡觉 has a value, cpap holds 0.0 (not blank), 0l and ص are blank;
    # 日 / 2026.0 / n color are structural and skipped even when blank.
    assert m._unfinished_0n_today() == [{"habit": "0l", "col": ""}, {"habit": "ص", "col": ""}]
    assert ("read_range", "0n", "A330:AP330") in fake.calls


def test_unfinished_empty_when_no_row_or_daemon_down(tmp_path, monkeypatch):
    _headers(tmp_path, monkeypatch, {"睡觉": 4, "0l": 19})
    _use(monkeypatch, FakeExcel(cells={"0n": {}}, rows={}))
    assert m._unfinished_0n_today() == []
    _use(monkeypatch, FakeExcel(rows={("0n", TODAY): 330}, fail={"read_range"}))
    assert m._unfinished_0n_today() == []
    monkeypatch.setattr(m, "_neon_excel", _boom)
    assert m._unfinished_0n_today() == []


def test_unfinished_empty_without_headers_file(tmp_path, monkeypatch):
    monkeypatch.setattr(m.Path, "home", classmethod(lambda cls: tmp_path))
    fake = _use(monkeypatch, FakeExcel())
    assert m._unfinished_0n_today() == []
    assert fake.calls == []


# ── _open_weekly_1g ──────────────────────────────────────────────────────────

_1G = {"1g": {
    "A3": "pri", "D3": "Goal", "E3": "分 ", "G3": "% Done",
    "A4": "i9", "F4": "500.0",
    "D5": "ship the doc", "E5": "100.0",
    "D6": "done already", "E6": "50.0", "G6": "1.0",
    "A13": "m5c7", "D13": "M",
    "D15": "Q4 leases", "E15": "80.0", "G15": "0.5",
    "A34": " xk87", "D35": "  park day  ", "E35": "n/a",
}}


def test_open_weekly_parses_rows_with_carried_domain(monkeypatch):
    fake = _use(monkeypatch, FakeExcel(cells=_1G))
    rows = m._open_weekly_1g("")
    assert fake.calls == [("read_range", "1g", "A1:G50")]
    assert rows == [
        {"text": "ship the doc", "domain": "i9", "fen": 100.0, "pct_done": 0.0},
        {"text": "Q4 leases", "domain": "m5c7", "fen": 80.0, "pct_done": 0.5},
        {"text": "Goal", "domain": "pri", "fen": 0.0, "pct_done": 0.0},
        {"text": "M", "domain": "m5c7", "fen": 0.0, "pct_done": 0.0},
        {"text": "park day", "domain": "xk87", "fen": 0.0, "pct_done": 0.0},
    ]


def test_open_weekly_domain_hint_sorts_first_and_maps_m5x2(monkeypatch):
    _use(monkeypatch, FakeExcel(cells=_1G))
    rows = m._open_weekly_1g("m5x2")
    assert [r["text"] for r in rows[:2]] == ["Q4 leases", "M"]
    assert all(r["text"] != "done already" for r in rows)


def test_open_weekly_empty_on_failure(monkeypatch):
    _use(monkeypatch, FakeExcel(fail={"read_range"}))
    assert m._open_weekly_1g("i9") == []
    monkeypatch.setattr(m, "_neon_excel", _boom)
    assert m._open_weekly_1g("i9") == []


# ── no raw AppleScript left in the read path ─────────────────────────────────

@pytest.mark.parametrize("fn", ["check_neon_column", "last_meal_today",
                                "_unfinished_0n_today", "_open_weekly_1g"])
def test_read_functions_do_not_shell_out(fn):
    src = inspect.getsource(getattr(m, fn))
    for raw in ("osascript", "subprocess", "Microsoft Excel", "ix-osa", "_ix_osa"):
        assert raw not in src, f"{fn} must read through the excel-http client, not {raw}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
