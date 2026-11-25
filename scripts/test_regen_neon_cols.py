"""regen-neon-cols.py reads row 1 of each sheet through the excel-http client.

2026-11-25: read_headers() piped an AppleScript through `ssh ix osascript -`.
It now takes one daemon range read (A1:BR1) per sheet. Same {sheet: {header:
letter}} map, and it still exits before main() can write neon-cols.json when
any sheet cannot be read: half a header map on disk breaks every cols.col().
"""
import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


@pytest.fixture()
def rg():
    spec = importlib.util.spec_from_file_location("regen_neon_cols", _HERE / "regen-neon-cols.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(**at):
    r = [""] * 70
    for letters, v in at.items():
        n = 0
        for ch in letters:
            n = n * 26 + ord(ch) - 64
        r[n - 1] = v
    return r


_ROWS = {
    "0分": _row(A="周", B="5^0", P="-1₦", AC="1₲"),
    "0n": _row(A="mee", C="2026.0", F="wake up ", AF="N color", AV="-1.0", AW="-2.0", BH="m5x2"),
    "1n+": _row(B="5.0", C="1s", AH="relax {60} ", AP="5^1 ₦ Efficiency Clearance"),
    "hcbi": _row(A="周", B="Date", Y="hcbp", AC="hcbp", BM="洗澡", BR="last"),
}


def _fake(monkeypatch, replies):
    sys.path.insert(0, str(_HERE.parent / "lib"))
    from neon import excel
    calls = []

    def read_range(sheet, rng):
        calls.append((sheet, rng))
        r = replies[sheet]
        return r if isinstance(r, dict) else {"ok": True, "row": 1, "rows": [r]}
    monkeypatch.setattr(excel, "read_range", read_range)
    return calls


def test_read_headers_maps_row_one_to_letters(rg, monkeypatch):
    calls = _fake(monkeypatch, _ROWS)
    out = rg.read_headers()
    assert calls == [("0分", "A1:BR1"), ("0n", "A1:BR1"), ("1n+", "A1:BR1"), ("hcbi", "A1:BR1")]
    assert list(out) == ["0分", "0n", "1n+", "hcbi"]
    assert out["0分"] == {"周": "A", "5^0": "B", "-1₦": "P", "1₲": "AC"}
    # stripped, and numeric headers lose the float tail ("2026.0" → "2026", "-2.0" → "-2")
    assert out["0n"] == {"mee": "A", "2026": "C", "wake up": "F", "N color": "AF",
                         "-1": "AV", "-2": "AW", "m5x2": "BH"}
    assert out["1n+"] == {"5": "B", "1s": "C", "relax {60}": "AH",
                          "5^1 ₦ Efficiency Clearance": "AP"}
    # a header that appears twice keeps its LAST column (as the old parser did)
    assert out["hcbi"] == {"周": "A", "Date": "B", "hcbp": "AC", "洗澡": "BM", "last": "BR"}


@pytest.mark.parametrize("bad", [
    {"ok": False, "error": "excel-http unreachable"},
    {"ok": False, "error": "queue_pending"},
    {"ok": True, "rows": []},
    {"ok": True, "rows": [["only", "three", "cells"]]},
    {},
])
def test_any_failed_sheet_exits_before_the_config_is_written(rg, monkeypatch, tmp_path, bad):
    cfg = tmp_path / "neon-cols.json"
    before = json.dumps({"_meta": {}, "sheets": {"0n": {"headers": {"keep": "A"}}}})
    cfg.write_text(before)
    monkeypatch.setattr(rg, "CONFIG", cfg)
    _fake(monkeypatch, {**_ROWS, "1n+": bad})
    with pytest.raises(SystemExit) as e:
        rg.main()
    assert "1n+" in str(e.value)
    assert cfg.read_text() == before


def test_main_writes_headers_and_keeps_curated_maps(rg, monkeypatch, tmp_path):
    cfg = tmp_path / "neon-cols.json"
    cfg.write_text(json.dumps({"_meta": {"last_regen": "2026-01-01"}, "sheets": {
        "0分": {"headers": {"stale": "Z"}, "domain_aliases": {"i9": "R"}, "date_col": "B"},
        "0n": {"headers": {}}, "1n+": {"headers": {}}, "hcbi": {"headers": {}}}}))
    monkeypatch.setattr(rg, "CONFIG", cfg)
    _fake(monkeypatch, _ROWS)
    rg.main()
    out = json.loads(cfg.read_text())
    assert out["sheets"]["0分"]["headers"] == {"周": "A", "5^0": "B", "-1₦": "P", "1₲": "AC"}
    assert out["sheets"]["0分"]["domain_aliases"] == {"i9": "R"}
    assert out["sheets"]["0分"]["date_col"] == "B"
    assert out["sheets"]["0n"]["headers"]["-2"] == "AW"


def test_no_applescript_or_ssh_left(rg):
    src = inspect.getsource(rg)
    for raw in ("osascript", "subprocess", '"ssh"', "Microsoft Excel"):
        assert raw not in src
