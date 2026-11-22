"""neon_grid (2026-11-21): the Neon workbook as grids of text + Excel's colours.
Built against a synthetic workbook so the tests need neither Excel nor Neon."""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import neon_grid as ng  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")
from openpyxl.formatting.rule import CellIsRule  # noqa: E402
from openpyxl.styles import Font, PatternFill  # noqa: E402

TODAY = dt.date(2026, 11, 21)


@pytest.mark.parametrize("value,fmt,want", [
    (1744, "#,##0", "1,744"), (572.142857, "#,##0", "572"), (0.5, "0%", "50%"), (0.123, "0.0%", "12.3%"),
    (3, "0", "3"), (2.5, "0.0", "2.5"), (-46, "#,##0", "-46"), (1034.5, "General", "1034.5"),
    (7, "General", "7"), (12, "[$-2000401]0", "١٢"), (12345.678, "0.00E+00", "1.23E+04"),
    (dt.datetime(2026, 11, 21), "m/d", "11/21"), (dt.datetime(2026, 1, 4), r"yyyy\.mm\.dd", "2026.01.04"),
    (dt.time(7, 5), "h:mm", "7:05"), (None, "General", ""), ("sf21", "General", "sf21"),
    ("#VALUE!", "General", "#VALUE!"), (True, "General", "TRUE"),
    (0, '_(* #,##0_);_(* (#,##0);_(* "-"??_);_(@_)', "-"), (5, '0"分"', "5分"),
])
def test_format_value(value, fmt, want):
    assert ng.format_value(value, fmt) == want


def test_general_rounds_to_fit_a_narrow_column():
    assert ng.format_value(1034.4285714, "General", width=4) == "1034"
    assert ng.format_value(-153.57142857, "General", width=5) == "-154"


@pytest.mark.parametrize("value,op,operands,want", [
    (1600, "greaterThan", [1500.0], True), (1500, "greaterThan", [1500.0], False),
    (700, "between", [500.0, 999.0], True), (1000, "between", [500.0, 999.0], False),
    (None, "lessThan", [5.0], True),            # a blank cell compares as 0
    (None, "equal", [0.0], True), (None, "greaterThan", [0.5], False), (None, "between", [1.0, 499.0], False),
    ("sf21", "equal", ["SF21"], True), ("SEA", "notEqual", ["sf21"], True), (None, "equal", ["sf21"], False),
    ("text", "greaterThan", [100.0], True),     # Excel orders text above every number
    ("#VALUE!", "greaterThan", [0.0], False), (3, "greaterThanOrEqual", [3.0], True),
    (5, "equal", [None], False),
])
def test_cell_is(value, op, operands, want):
    assert ng.cell_is(value, op, operands) is want


def _workbook(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "0分"
    ws.append(["周", "5^0", "地", "Σ", "卯", "note"])
    for i in range(10):
        d = dt.datetime(2026, 11, 14) + dt.timedelta(days=i)
        ws.append([None, d, "sf21" if i % 2 else "SEA", 400 * i, i if i % 3 else None, "x"])
        ws.cell(row=i + 2, column=2).number_format = "m/d"
        ws.cell(row=i + 2, column=4).number_format = "#,##0"
    ws["A2"] = 11.2
    ws.merge_cells("A2:A8")
    for r in range(2, 12):
        ws.cell(row=r, column=4).fill = PatternFill("solid", fgColor="FF3DD4F5")
    ws["F3"].font = Font(color="FFFFFFFF")               # white on white: hidden in Excel
    ws["F4"].font = Font(color="FFA6A6A6")
    ws.conditional_formatting.add("D2:D1048576", CellIsRule(
        operator="greaterThan", formula=["1500"], stopIfTrue=True,
        fill=PatternFill(bgColor="FF388195"), font=Font(color="FFFFFFFF", bold=True)))
    ws.conditional_formatting.add("D2:D11", CellIsRule(
        operator="between", formula=["1", "1500"], fill=PatternFill(bgColor="FFAAAAAA")))
    ws.conditional_formatting.add("C2:C11", CellIsRule(
        operator="equal", formula=['"sf21"'], fill=PatternFill(bgColor="FFD9D9D9")))
    ws.conditional_formatting.add("E2:E11", CellIsRule(
        operator="lessThan", formula=["1"], fill=PatternFill(bgColor="FF808080")))
    ws.column_dimensions["F"].hidden = False
    ws.column_dimensions["D"].width = 5.3
    ws.row_dimensions[10].hidden = True
    ws["A2000"] = "stray"                                 # must not make the sheet 2000 rows long
    ws.sheet_properties.tabColor = "FF123456"
    hidden = wb.create_sheet("old")
    hidden.sheet_state = "hidden"
    wb.create_sheet("meta").append(["Time", "Cat"])
    wb.save(path)
    return path


@pytest.fixture()
def snap(tmp_path):
    return ng.build(_workbook(tmp_path / "neon.xlsx"), today=TODAY)


def _cell(sheet, row, col):
    c = sheet["rows"][row][sheet["cols"].index(col)]
    return c[0], sheet["styles"][c[1]]


def test_build_sheets_text_and_extent(snap):
    assert [s["name"] for s in snap["sheets"]] == ["0分", "meta"]        # hidden sheet dropped
    s = snap["sheets"][0]
    assert s["tab"] == "#123456" and s["header_rows"] == 1 and s["frozen_cols"] == 2
    assert len(s["rows"]) == 10 and 10 not in s["row_nums"]              # hidden row gone, stray row 2000 cut
    assert _cell(s, 0, "A")[0] == "周" and _cell(s, 1, "B")[0] == "11/14" and _cell(s, 5, "D")[0] == "1,600"
    assert s["widths"][s["cols"].index("D")] == 5
    assert s["skipped_rules"] == 0


def test_build_colours_static_then_rules(snap):
    s = snap["sheets"][0]
    assert _cell(s, 1, "D")[1][:2] == ["#3dd4f5", "#000000"]            # value 0: static fill, black ink
    assert _cell(s, 2, "D")[1][0] == "#aaaaaa"                           # 400: the between rule
    text, st = _cell(s, 5, "D")                                          # 1600: >1500 wins, stops the rest
    assert st[:3] == ["#388195", "#ffffff", 1]
    assert _cell(s, 2, "C")[1][0] == "#d9d9d9" and _cell(s, 1, "C")[1][0] is None
    assert _cell(s, 1, "E")[1][0] == "#808080"                           # blank compares as 0 → lessThan 1
    assert _cell(s, 2, "E")[1][0] is None


def test_unfilled_cells_adapt_to_a_dark_terminal(snap):
    s = snap["sheets"][0]
    assert _cell(s, 1, "F") == ("x", [None, None, 0, "l"])               # black-on-white → terminal default
    assert _cell(s, 2, "F")[0] == ""                                     # white-on-white stays invisible
    assert _cell(s, 3, "F") == ("x", [None, "#a6a6a6", 0, "l"])          # a grey font keeps its colour


def test_vertical_merge_points_at_its_anchor(snap):
    s = snap["sheets"][0]
    a = s["cols"].index("A")
    assert s["rows"][1][a][0] == "11.2" and len(s["rows"][1][a]) == 2
    assert s["rows"][4][a] == ["", s["rows"][4][a][1], 3]                # text lives 3 rows up


def test_today_row_and_fallback_to_latest_past_day(tmp_path):
    path = _workbook(tmp_path / "neon.xlsx")
    s = ng.build(path, today=TODAY)["sheets"][0]
    assert _cell(s, s["today_row"], "B")[0] == "11/21"
    s = ng.build(path, today=dt.date(2026, 12, 25))["sheets"][0]
    assert _cell(s, s["today_row"], "B")[0] == "11/23"
    assert ng.build(path, today=TODAY)["sheets"][1]["today_row"] is None


def test_snapshot_is_only_rewritten_when_content_changes(tmp_path):
    src, out = _workbook(tmp_path / "neon.xlsx"), tmp_path / "cache" / "grid.json"
    assert ng.write_snapshot(src, out) == "written"
    first = out.stat().st_mtime_ns
    snap = ng.load_snapshot(out)
    assert snap["version"] == ng.VERSION and snap["sheets"][0]["name"] == "0分"
    wb = openpyxl.load_workbook(src)
    wb.save(src)                                           # a re-save with no cell changed
    assert ng.write_snapshot(src, out) == "unchanged" and out.stat().st_mtime_ns == first
    wb["0分"]["D3"] = 9999
    wb.save(src)
    assert ng.write_snapshot(src, out) == "written"
    assert ng.load_snapshot(tmp_path / "missing.json") is None


def test_theme_and_error_string_colours():
    class C:  # openpyxl hands back an error STRING for .rgb on a theme colour
        type, theme, tint, rgb = "theme", 1, 0.0, "Values must be of type <class 'str'>"
    assert ng.resolve_color(C(), ["ffffff", "000000"]) == "000000"
    C.theme = 9
    assert ng.resolve_color(C(), ["ffffff", "000000"]) is None
    assert ng._tint("808080", 0.5) == "c0c0c0" and ng._tint("808080", -0.5) == "404040"
    assert ng.cwidth("卯辰") == 4 and ng.cwidth("ab") == 2


def test_html_dump(snap):
    page = ng.to_html(snap["sheets"][0])
    assert "background:#388195" in page and "1,600" in page
