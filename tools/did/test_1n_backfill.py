#!/usr/bin/env python3
"""/1n backfill (2026-10-05): mark a weekly 1₦+ habit for a past week, credit
today. Pure-helper tests: week parsing (fiscal labels, dates, 'last week'),
the 0分 column lookup (incl. hyphenated headers like '1 -2g', which
header_normalize turns into '1 2g'), and the already-marked guard."""
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("on_backfill", _HERE / "1n-backfill.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["on_backfill"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ob():
    return _load()


@pytest.fixture(scope="module")
def df(ob):
    return ob._did()


TODAY = date(2026, 10, 5)  # Monday of week 10.1 (Sun 10/4)


@pytest.mark.parametrize("raw,label,sunday", [
    ("9.5", "9.5", date(2026, 9, 27)),
    ("9.4", "9.4", date(2026, 9, 20)),
    ("last week", "9.5", date(2026, 9, 27)),
    ("9/24", "9.4", date(2026, 9, 20)),
    ("2026-09-26", "9.4", date(2026, 9, 20)),
    ("yesterday", "10.1", date(2026, 10, 4)),
    ("this week", "10.1", date(2026, 10, 4)),
])
def test_parse_week(ob, raw, label, sunday):
    assert ob.parse_week(raw, TODAY) == (label, sunday)


def test_parse_week_unknown_label(ob):
    with pytest.raises(ValueError):
        ob.parse_week("13.1", TODAY)


@pytest.mark.parametrize("key,col", [
    ("1 2g", "T"),      # header '1 -2g' normalized; raw-keyed map missed it
    ("1 1n", "T"),      # header '1 -1n' (was P until 2026-10-25: P is reconciled)
    ("1 xk88", "Y"),
    ("1 m5x2", "S"),    # generic '1 <domain>' fallback
    ("family", "X"),
    ("nails", None),
])
def test_resolve_fen_col(ob, df, key, col):
    assert ob.resolve_fen_col(df, key, None) == col


def test_resolve_fen_col_domain_override(ob, df):
    assert ob.resolve_fen_col(df, "nails", "hcb") == "W"


@pytest.mark.parametrize("formula,marked", [
    ("", False), ("0", False), ("=0+0", False),
    ("1", True), ("=0+1", True), ("69", True), ("x", True),
])
def test_is_marked(ob, formula, marked):
    assert ob.is_marked(formula) is marked


# --- the read goes through excel-http (2026-11-25, Neon off Excel) -----------
# read_script (raw AppleScript: scan col B for the week, the cell's formula,
# row 5's display text) is gone; read_week_cell asks the daemon and returns
# the dict parse_read gave for that script.

class FakeExcel:
    def __init__(self, weeks=None, cells=None, refuse=None):
        self.weeks = weeks if weeks is not None else {"11.3": 51}
        self.cells = cells if cells is not None else {
            ("C", 51): ("1.0", "1"), ("C", 5): ("45.0", "45"),
            ("V", 51): ("42.0", "=30+12"), ("V", 5): (".5/m", ".5/m"),
            ("AF", 51): ("", ""), ("AF", 5): ("1/m", "1/m")}
        self.refuse = refuse or {}
        self.calls = []

    def lookup(self, sheet, label):
        self.calls.append(("lookup", sheet, label))
        if "lookup" in self.refuse:
            return self.refuse["lookup"]
        row = self.weeks.get(label)
        return {"ok": True, "row": row} if row else {"ok": False, "error": "date_not_found"}

    def read(self, sheet, col, *, date=None, row=None):
        self.calls.append(("read", sheet, col, row))
        if row in self.refuse:
            return self.refuse[row]
        value, formula = self.cells.get((col, row), ("", ""))
        return {"ok": True, "row": row, "col": col, "value": value, "formula": formula}

    def _write(self, *a, **k):
        raise AssertionError("the read half called a write endpoint")
    append = write = batch_append = batch_write = _write


@pytest.fixture
def fake(ob, monkeypatch):
    def install(**kw):
        fx = FakeExcel(**kw)
        monkeypatch.setattr(ob, "neon_excel", fx)
        monkeypatch.setattr(ob, "ix_run", lambda *a, **k: pytest.fail("raw AppleScript run"))
        return fx
    return install


def test_read_week_cell_gives_the_old_script_shape(ob, fake):
    fx = fake()
    # ROW stays text, CELL is the formula, E is row 5 as the sheet shows it
    assert ob.read_week_cell("C", "11.3") == {"ROW": "51", "CELL": "1", "E": "45"}
    assert fx.calls == [("lookup", "1n+", "11.3"), ("read", "1n+", "C", 51), ("read", "1n+", "C", 5)]
    assert ob.read_week_cell("V", "11.3") == {"ROW": "51", "CELL": "=30+12", "E": ".5/m"}
    assert ob.read_week_cell("AF", "11.3") == {"ROW": "51", "CELL": "", "E": "1/m"}
    assert not ob.is_marked(ob.read_week_cell("AF", "11.3")["CELL"])


@pytest.mark.parametrize("value,shown", [
    ("45.0", "45"), ("3.0", "3"), ("120.0", "120"), ("0.0", "0"), ("-5.0", "-5"),
    ("22.5", "22.5"), ("1.714285714286", "1.714285714286"),
    ("1/m", "1/m"), (".5/m", ".5/m"), ("15+1/m", "15+1/m"), ("", ""), ("nan", "nan"),
])
def test_display_num_matches_the_sheets_display_text(ob, value, shown):
    """The daemon returns values ("45.0"); the old script read display text
    ("45"). float(row5) and the JSON "points" field saw the display form."""
    assert ob.display_num(value) == shown


@pytest.mark.parametrize("weeks", [
    {},                      # label not on the sheet
    {"11.3": 3},             # the old scan covered rows 4..100 only
    {"11.3": 101},
])
def test_read_week_cell_week_not_found_keeps_the_old_message(ob, fake, weeks):
    fake(weeks=weeks)
    with pytest.raises(ob.ExcelReadError) as e:
        ob.read_week_cell("C", "11.3")
    assert str(e.value) == "ERROR: week 11.3 not found in 1n+"


@pytest.mark.parametrize("refuse,needle", [
    ({"lookup": None}, "1n+ week lookup for 11.3: excel-http unreachable"),
    ({"lookup": {"ok": False, "error": "excel_error", "detail": "busy"}}, "excel_error (busy)"),
    ({51: {"ok": False, "error": "queue_pending"}}, "1n+!C51: queue_pending"),
    ({5: {"ok": False, "error": "queue_pending"}}, "1n+!C5: queue_pending"),
    ({5: None}, "1n+!C5: read failed"),
])
def test_read_week_cell_failed_read_is_an_error_never_a_blank(ob, fake, refuse, needle):
    """A refused cell read must not read as '' (unmarked): the week would be
    marked and credited a second time."""
    fake(refuse=refuse)
    with pytest.raises(ob.ExcelReadError) as e:
        ob.read_week_cell("C", "11.3")
    assert needle in str(e.value) and "not found" not in str(e.value)


def _main(ob, df, monkeypatch, argv):
    monkeypatch.setattr(df, "load_headers", lambda: {"0n": {}, "1n": {"1s": "C", "family": "AF"}})
    monkeypatch.setattr(ob, "_did", lambda: df)
    monkeypatch.setattr(ob.daytime, "today", lambda: date(2026, 11, 25))
    monkeypatch.setattr(sys, "argv", ["1n-backfill.py"] + argv)
    return ob.main()


def test_main_dry_run_reads_through_the_daemon_and_writes_nothing(ob, df, fake, monkeypatch, capsys):
    import json
    fx = fake(cells={("C", 51): ("", ""), ("C", 5): ("45.0", "45")})
    assert _main(ob, df, monkeypatch, ["1s", "11.3", "--domain", "g245", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["row"] == "51" and out["pre_cell"] == "" and out["points"] == "45"
    assert out["dry_run"] is True and out["would_write_cell"] == 1
    assert {c[0] for c in fx.calls} == {"lookup", "read"}


def test_main_already_marked_week_awards_nothing(ob, df, fake, monkeypatch, capsys):
    import json
    fake()                                             # C51 formula "1": marked
    assert _main(ob, df, monkeypatch, ["1s", "11.3", "--domain", "g245"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["already_credited"] is True and out["points_awarded"] == 0


@pytest.mark.parametrize("kw,needle", [
    ({"weeks": {}}, "ERROR: excel read failed: ERROR: week 11.3 not found in 1n+"),
    ({"refuse": {51: {"ok": False, "error": "queue_pending"}}},
     "ERROR: excel read failed: 1n+!C51: queue_pending"),
])
def test_main_failed_read_exits_like_a_failed_script_before_any_write(ob, df, fake, monkeypatch, kw, needle):
    fake(**kw)
    with pytest.raises(SystemExit) as e:
        _main(ob, df, monkeypatch, ["1s", "11.3", "--domain", "g245"])
    assert str(e.value) == needle


def test_no_raw_read_script_is_left(ob):
    src = (_HERE / "1n-backfill.py").read_text()
    assert not hasattr(ob, "read_script") and not hasattr(ob, "parse_read")
    assert "tell application" not in src


def test_did_fast_hyphenated_header_reaches_0fen(df):
    """/did routing: '1 -2g' must resolve to its 0分 column, not None."""
    item = df.ParsedItem(raw="1 -2g", name="1 -2g")
    headers = {"0n": {}, "1n": {"1 -2g": "K", "1 -1n": "M"}}
    tq = {"0neon": [], "夜neon": [], "1neon": []}
    res = df.route_items([item], headers, tq)
    one_n = [r for r in res if r.step == "1n"]
    assert one_n and one_n[0].fen_col == "T"


def test_weekly_cards_never_credit_reconciled_p_column():
    """0分!P is rebuilt by the build-order daemon's -1n reconcile every block;
    a weekly card credited there is silently erased (1 -1n, 2026-10-19)."""
    import pathlib, re
    text = pathlib.Path(__file__).with_name("did-fast.py").read_text()
    start = text.index("ONENEON_TO_0FEN: dict[str, str] = {")
    block = text[start:text.index("\n}\n", start)]
    assert re.search(r'"1 -1n": "T"', block)
    assert not re.search(r':\s*"P"', block)
