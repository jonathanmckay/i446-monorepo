"""neon-task-checksum reads 0n / 1n+ through the excel-http client.

2026-11-25: read_sheets() ran one AppleScript (READ_SHEETS via osascript, or
`ssh ix osascript -` off Ix) and parsed tab-joined lines. It now takes two
daemon range reads. Contract kept: (headers_0n, [row1, row2, row3, row5] of
1n+, pts_0n), and a RuntimeError when the sheet cannot be read, because a
checksum run on missing data would report every card missing.
"""
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


@pytest.fixture()
def cs():
    spec = importlib.util.spec_from_file_location("ntc_reads", _HERE / "neon-task-checksum.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ntc_reads"] = mod
    spec.loader.exec_module(mod)
    return mod


# D1:BL2 is 61 columns, C1:AL5 is 36; live shapes (2026-11-25), shortened values.
def _row(n, **at):
    r = [""] * n
    for i, v in at.items():
        r[int(i[1:])] = v
    return r


_0N = [
    _row(61, c0="睡觉", c1="cpap", c2="wake up ", c8="1st hci", c29="N color", c30="⎣∀clr",
         c43="-1.0", c45=""),
    _row(61, c0="30.0", c1="20.0", c2="6.0", c8="26.0", c29="111.0", c43="10.0"),
]
_1N = [
    _row(36, c0="1s", c3="s+hcbp ", c20="groceries", c22="1 sunset", c35="1 kids nature "),
    _row(36, c0="20.0", c3="30.0", c20="15.0", c22=" ", c35="120.0"),
    _row(36, c0="1.0", c3="1.0", c20="5.0", c22="6.0", c35="7.0"),
    _row(36, c0="row four is not read", c20="x"),
    _row(36, c0="45.0", c3="1/m", c20="20.0", c22="20.0", c35="120.0"),
]


def _fake(monkeypatch, replies):
    """replies: {(sheet, range): reply}. Patches neon.excel.read_range."""
    sys.path.insert(0, str(_HERE.parent / "lib"))
    from neon import excel
    calls = []

    def read_range(sheet, rng):
        calls.append((sheet, rng))
        return replies[(sheet, rng)]
    monkeypatch.setattr(excel, "read_range", read_range)
    return calls


def _ok(rows):
    return {"ok": True, "row": 1, "rows": rows}


def test_read_sheets_shapes_and_values(cs, monkeypatch):
    calls = _fake(monkeypatch, {("0n", "D1:BL2"): _ok(_0N), ("1n+", "C1:AL5"): _ok(_1N)})
    headers_0n, rows_1n, pts_0n = cs.read_sheets()
    assert calls == [("0n", "D1:BL2"), ("1n+", "C1:AL5")]
    # blank header cells dropped, text untouched (trailing space kept, as before)
    assert headers_0n == ["睡觉", "cpap", "wake up ", "1st hci", "N color", "⎣∀clr", "-1.0"]
    assert pts_0n == {"睡觉": 30, "cpap": 20, "wake up": 6, "1st hci": 26,
                      "n color": 111, "⎣∀clr": None, "1.0": 10}
    # rows 1, 2, 3 and 5: row 4 is skipped
    assert rows_1n == [_1N[0], _1N[1], _1N[2], _1N[4]]
    exp = cs.parse_1n_expectations(*rows_1n)
    assert exp == [
        {"header": "1s", "time": 20, "day": 1, "pts": 45, "rate": None},
        {"header": "s+hcbp", "time": 30, "day": 1, "pts": None, "rate": "1/m"},
        {"header": "groceries", "time": 15, "day": 5, "pts": 20, "rate": None},
        {"header": "1 sunset", "time": None, "day": 6, "pts": 20, "rate": None},
        {"header": "1 kids nature", "time": 120, "day": 7, "pts": 120, "rate": None},
    ]


def test_points_stay_aligned_past_blank_header_cells(cs, monkeypatch):
    # 0n has gaps in row 1 (AT, AY, BE, BF live); a credit must not slide left.
    hdr = _row(61, c0="a", c2="c")
    pts = _row(61, c0="1.0", c1="99.0", c2="3.0")
    _fake(monkeypatch, {("0n", "D1:BL2"): _ok([hdr, pts]), ("1n+", "C1:AL5"): _ok(_1N)})
    _, _, pts_0n = cs.read_sheets()
    assert pts_0n == {"a": 1, "c": 3}


@pytest.mark.parametrize("bad", [
    {"ok": False, "error": "excel-http unreachable"},
    {"ok": False, "error": "queue_pending"},
    {"ok": True, "rows": []},
    {"ok": True, "rows": [_0N[0]]},
    {},
])
def test_unreadable_0n_raises_runtime_error(cs, monkeypatch, bad):
    _fake(monkeypatch, {("0n", "D1:BL2"): bad, ("1n+", "C1:AL5"): _ok(_1N)})
    with pytest.raises(RuntimeError):
        cs.read_sheets()


def test_unreadable_1n_raises_runtime_error(cs, monkeypatch):
    _fake(monkeypatch, {("0n", "D1:BL2"): _ok(_0N),
                        ("1n+", "C1:AL5"): {"ok": False, "error": "excel_error"}})
    with pytest.raises(RuntimeError, match="1n\\+"):
        cs.read_sheets()
    _fake(monkeypatch, {("0n", "D1:BL2"): _ok(_0N), ("1n+", "C1:AL5"): _ok(_1N[:4])})
    with pytest.raises(RuntimeError, match="expected 5 rows"):
        cs.read_sheets()


def test_no_applescript_left_in_the_sheet_read(cs):
    src = inspect.getsource(cs)
    assert "osascript" not in src.replace("old osascript read", "")
    assert not hasattr(cs, "run_osascript") and not hasattr(cs, "READ_SHEETS")
    assert "read_range" in inspect.getsource(cs._read_range)
