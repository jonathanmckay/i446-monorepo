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
    wb.create_sheet("meta").append(["Time", "Cat"])       # in SKIP_SHEETS
    wb.create_sheet("hcbi").append(["Time", "Cat"])
    wb.save(path)
    return path


@pytest.fixture()
def snap(tmp_path, monkeypatch):
    # the engine tests want the sheet as Excel lays it out, not 0分's compact view
    monkeypatch.setitem(ng.SHEETS, "0分", {
        "header_rows": 1, "date_col": "B", "frozen_cols": 2, "col_style": {"A": ["000000", "ffffff"]}})
    return ng.build(_workbook(tmp_path / "neon.xlsx"), today=TODAY)


def _cell(sheet, row, col):
    c = sheet["rows"][row][sheet["cols"].index(col)]
    return c[0], sheet["styles"][c[1]]


def test_build_sheets_text_and_extent(snap):
    assert [s["name"] for s in snap["sheets"]] == ["0分", "hcbi"]        # hidden and skipped sheets dropped
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
    assert _cell(s, s["today_row"], "B")[0] == "11.21"
    s = ng.build(path, today=dt.date(2026, 12, 25))["sheets"][0]
    assert _cell(s, s["today_row"], "B")[0] == "11.23"
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


def test_0fen_view_drops_n156_and_blackens_the_week_column(tmp_path, monkeypatch):
    path = _workbook(tmp_path / "neon.xlsx")
    wb = openpyxl.load_workbook(path)
    ws = wb["0分"]
    ws["G1"], ws["G3"], ws["H3"] = "n156", 7, 9            # n156 and an unlabelled column after it
    wb.save(path)
    s = ng.build(path, today=TODAY)["sheets"][0]
    assert s["cols"] == ["B", "D", "E", "F"]                              # 地 and 周 hidden
    assert s["widths"] == [5, 5, 3, 3] and s["frozen_cols"] == 1         # compact: 3 wide, Σ and date 5
    assert _cell(s, 1, "B") == ("11.14", ["#000000", "#ffffff", 0, "r"])  # mm.dd, white on black
    assert _cell(s, 5, "D")[0] == "1600" and s["live"]["cells"]["D"]["fmt"] == "0"
    assert _cell(s, 1, "E")[1][:2] == ["#000000", "#ffffff"]             # the dark grey (lessThan 1) fill is black
    row = s["live"]["row"]
    ng.overlay(s, {"E": 0.5})
    assert _cell(s, row, "E")[1][:2] == ["#000000", "#ffffff"]           # and stays black through the ledger overlay
    monkeypatch.setitem(ng.SHEETS["0分"], "date_label", "week")
    s = ng.build(path, today=TODAY)["sheets"][0]
    assert _cell(s, 5, "D")[0] == "1600"                                 # Σ loses its thousands comma
    assert s["live"]["cells"]["D"]["fmt"] == "0"                         # and so does the ledger overlay
    assert _cell(s, 1, "B") == ("11.2.Sa", ["#000000", "#ffffff", 0, "r"])   # 2026-11-14, week 11.2
    assert _cell(s, 4, "B")[0] == "11.2.Tu"                              # week read through the merge
    assert _cell(s, 8, "B")[0] == "11/21"                                # no 周 value: plain date
    assert _cell(s, 0, "B")[1][0] is None                                # the header row keeps its own style
    assert ng.week_date(8.4, dt.date(2026, 8, 23)) == "08.4.Su" and ng.week_date("1.1", dt.date(2026, 1, 4)) == "01.1.Su"


def test_overlay_repaints_today_from_fresher_values(snap):
    s = snap["sheets"][0]
    row = s["live"]["row"]
    assert row == s["today_row"] and _cell(s, row, "D")[0] == "2,800"
    assert "A" not in s["live"]["cells"]                                 # merged / forced cells are left alone
    n = ng.overlay(s, {"D": 900.4, "E": 0, "ZZ": 5, "C": None})
    assert n == 2
    assert _cell(s, row, "D") == ("900", ["#aaaaaa", "#000000", 0, "r"])  # now inside the 1–1500 rule
    assert _cell(s, row, "E")[1][0] == "#808080"                          # 0 trips lessThan 1
    assert ng.overlay(s, {"D": 900.4}) == 0                               # idempotent
    json.dumps(s)                                                         # still plain data
    assert ng.overlay({"live": None}, {"D": 1}) == 0
    blank = s["cols"].index("E")
    s["rows"][row][blank] = ["", s["rows"][row][blank][1]]
    assert ng.overlay(s, {"E": 0.0}) == 0 and s["rows"][row][blank][0] == ""   # a blank cell stays blank on a 0


def test_snapshot_names_its_day_and_weekly_sheets_get_a_live_row(tmp_path, monkeypatch):
    """2026-11-22: readers overlay ledger writes on the today row, so the
    snapshot says which day that row is for (and midnight changes the hash),
    and a weekly sheet's this-week row carries a recipe like a daily one."""
    import openpyxl
    path = _workbook(tmp_path / "neon.xlsx")
    wb = openpyxl.load_workbook(path)
    ws = wb.create_sheet("wk")
    ws.append(["周", "x"])
    for label in (11.1, 11.2, 11.3):
        ws.append([label, 1])
    wb.save(path)
    day = dt.date(2026, 11, 20)                                  # in the fixture's merged week 11.2
    a = ng.build(path, today=day)
    assert a["day"] == "2026-11-20"
    monkeypatch.setattr(ng, "build_sheet", lambda *x, **k: {"name": "same"})
    assert ng.build(path, today=day)["hash"] != ng.build(path, today=TODAY)["hash"]   # midnight alone moves the hash
    daily = a["sheets"][0]
    assert daily["live"]["excel_row"] == daily["row_nums"][daily["today_row"]]
    weekly = [s for s in a["sheets"] if s["name"] == "wk"][0]
    assert weekly["today_row"] == 2 and weekly["live"]["excel_row"] == 3 and "B" in weekly["live"]["cells"]
    weekly["rows"][2][1] = ["", weekly["rows"][2][1][1]]
    assert ng.overlay(weekly, {"B": 0}) == 0                     # a derived 0 leaves a blank cell blank
    assert ng.overlay(weekly, {"B": 0}, zeros=True) == 1 and weekly["rows"][2][1][0] == "0"   # a written 0 shows


def test_view_overrides_relabel_move_refill_and_recolour(tmp_path, monkeypatch):
    """JM 2026-11-22: push shows as i446 between charge and tmrw, ibx i9 gets
    its own colour, 1st hci is light blue from 1. All view-side: a label, an
    order, a static fill and a column's colour rules, in the grid and in the
    live recipe overlay() repaints from."""
    path = _workbook(tmp_path / "neon.xlsx")
    monkeypatch.setitem(ng.SHEETS, "0分", {"header_rows": 1, "date_col": "B", "frozen_cols": 2})
    plain = ng.build(path, today=TODAY)["sheets"][0]
    assert plain["cols"].index("E") > plain["cols"].index("D")
    head_e = _cell(plain, 0, "E")[0]
    monkeypatch.setitem(ng.SHEETS, "0分", {
        "header_rows": 1, "date_col": "B", "frozen_cols": 2,
        "labels": {"E": "i446"}, "after": {"E": "B"}, "widths": {"E": 4},
        "fills": {"D": {"body": None}},
        "cf": {"D": [[1, "greaterThan", [0.5], "112233", "ffffff", False, False]]}})
    s = ng.build(path, today=TODAY)["sheets"][0]
    assert s["cols"].index("E") == s["cols"].index("B") + 1          # drawn right after B
    assert sorted(s["cols"]) == sorted(plain["cols"])                 # nothing lost or doubled
    assert _cell(s, 0, "E")[0] == "i446" != head_e
    assert s["widths"][s["cols"].index("E")] == 4
    assert s["widths"][s["cols"].index("D")] == plain["widths"][plain["cols"].index("D")]
    row = s["live"]["row"]
    assert _cell(plain, row, "D")[1][0] not in (None, "#112233")      # the workbook's own fill and rules
    assert _cell(s, row, "D")[1][:2] == ["#112233", "#ffffff"]        # replaced by the view's rule
    assert s["live"]["cells"]["D"]["base"][0] is None                 # static fill dropped
    assert [r[3] for r in s["live"]["cells"]["D"]["rules"]] == ["112233"]
    ng.overlay(s, {"D": 0}, zeros=True)
    assert _cell(s, row, "D")[1][0] is None                           # blank again: no fill, not the old one
    ng.overlay(s, {"D": 1}, zeros=True)
    assert _cell(s, row, "D")[1][0] == "#112233"
    assert _cell(s, 0, "D")[1] == _cell(plain, 0, "D")[1]             # row 1 keeps its own style


def test_0n_view_carries_the_2026_11_22_overrides():
    v = ng.SHEETS["0n"]
    assert v["labels"] == {"X": "i446"} and v["after"] == {"X": "I"} and v["widths"]["X"] == 4
    assert v["cf"]["L"][0][1:3] == ["greaterThan", [0.1]]             # 1st hci: 1 is enough
    stats_blue = "2979ff"
    assert v["cf"]["W"][0][3] not in (stats_blue, "00a3ee")           # ibx i9 != stats/notes, != push's old header
    assert v["cf"]["X"][0][3] != "b3b3b3" and v["fills"]["X"]["body"] is None


def test_split_sheet_cuts_tabs_out_of_one_sheet():
    """JM 2026-11-25: "1n+ Let's break it into 1n, 2n, 3n"."""
    cols = ["A", "B", "C", "D", "E"]
    rows = [[["", 0], ["wk", 0], ["x", 0], ["", 0], ["q", 0]]]
    rows += [[["sf", 0] if r == 1 else ["", 0, r - 1], ["1.%d" % r, 0], [str(r), 0], ["", 0], ["", 0]] for r in range(1, 4)]
    rows += [[["", 0, 4], ["month", 0], ["m", 0], ["", 0], ["Q1", 0]]]
    full = {"name": "1n+", "tab": "#111111", "header_rows": 1, "frozen_cols": 2, "today_row": 2,
            "cols": cols, "row_nums": [1, 2, 3, 4, 6], "widths": [4, 5, 6, 3, 7], "styles": [[None, None, 0, "l"]],
            "rows": rows, "skipped_rules": 0, "live": {"row": 2, "excel_row": 3, "cells": {"C": {}}}}
    wk, mo, last = ng.split_sheet(full, [
        {"name": "1n", "rows": [1, 4]},
        {"name": "2n", "rows": [6, 9], "cols": ["A", "C"], "header_rows": 1, "frozen": "B", "widths": {"B": 14}},
        {"name": "3n", "rows": [6, 6], "cols": ["E", "E"], "last": True}])
    assert (wk["name"], wk["source"], wk["cols"], wk["frozen_cols"]) == ("1n", "1n+", ["A", "B", "C", "E"], 2)
    assert wk["today_row"] == 2 and wk["live"]["row"] == 2 and wk["live"]["excel_row"] == 3
    assert wk["rows"][3][0] == ["", 0, 2]                   # the merge still points at its label
    assert mo["cols"] == ["B", "C"] and mo["widths"] == [14, 6] and mo["frozen_cols"] == 1   # blank A dropped
    assert mo["rows"] == [[["month", 0], ["m", 0]]] and mo["today_row"] is None and mo["live"] is None
    assert mo["row_nums"] == [6] and last["last"] is True and last["rows"] == [[["Q1", 0]]]
    wk["styles"].append(["#000000", None, 0, "l"])
    assert len(mo["styles"]) == 1                           # parts do not share one style table


def test_ratings_and_points_are_colour_coded():
    """JM 2026-11-25: "i9 + m5x2 color code the ratings and the points"."""
    view = ng.SHEETS["i9+m5x2"]
    def colour(col, value):
        return ng.paint(value, "General", [None, None, False, None], view["cf"][col], 6)[1][0]
    assert colour("B", "EE") == colour("G", "EE ") == "#1c7c3c"       # a rating typed with a trailing space
    assert len({colour("B", r) for r in ("EE", "MA", "MM", "MS", "OL")}) == 5
    assert colour("B", "") is None
    assert colour("C", 425) == "#10253f" and colour("C", 90) == "#a5b6cb" and colour("C", 0) == "#d9d9d9"
    assert colour("J", 237) == "#602827" and colour("J", 147) == "#d99694"
    names = [p["name"] for p in ng.SHEETS["1n+"]["split"]]
    assert names == ["1n", "2n", "3n", "4n"]
