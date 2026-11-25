"""Regression (2026-11-05): "一起饭 asked for points when this should be known by
the algorithm and time". A header refresh that read nothing from Excel (exit 0,
empty stdout) overwrote headers.json with no habits and stamped it fresh, so for
24 h every 0n/1n+ name looked unknown and janus asked for points.

2026-11-25: refresh_headers reads row 1 through excel-http (/read_range) instead
of a raw AppleScript, so these fake the excel client."""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture
def df(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("df_headers", HERE / "did-fast.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["df_headers"] = m
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "HEADERS_PATH", tmp_path / "headers.json")
    monkeypatch.setattr(m, "ix_run", lambda *a, **k: pytest.fail("refresh_headers ran a raw script"))
    return m


def _row(width, cells):
    row = [""] * width
    for i, text in cells.items():
        row[i] = text
    return row


def _excel(df, monkeypatch, n0=None, n1=None, fail=None):
    """Fake excel.read_range: n0 = {0-based index in A1:BJ1: text}, n1 = the
    same for C1:AN1. `fail` = sheet whose read comes back not ok."""
    calls = []

    def read_range(sheet, rng):
        calls.append((sheet, rng))
        if fail == sheet:
            return {"ok": False, "error": "queue_pending"}
        if sheet == "0n":
            return {"ok": True, "range": rng, "row": 1, "rows": [_row(62, n0 or {})]}
        return {"ok": True, "range": rng, "row": 1, "rows": [_row(38, n1 or {})]}
    monkeypatch.setattr(df.neon_excel, "read_range", read_range)
    return calls


GOOD = ({2: "0l"}, {21: "一起饭"})          # 0n col 3 (C), 1n+ col X (C + 21)
READS = [("0n", "A1:BJ1"), ("1n+", "C1:AN1")]


def _cache(df, n0, n1, refreshed):
    df.HEADERS_PATH.write_text(json.dumps({"refreshed": refreshed, "0n": n0, "1n": n1}))


def test_empty_read_keeps_the_last_good_headers(df, monkeypatch):
    _cache(df, {"0l": 3}, {"一起饭": "X"}, "2026-01-01T00:00:00-08:00")
    _excel(df, monkeypatch)
    assert "一起饭" in df.refresh_headers()["1n"]
    assert "一起饭" in json.loads(df.HEADERS_PATH.read_text())["1n"]


def test_one_sheet_empty_counts_as_empty(df, monkeypatch):
    _cache(df, {"0l": 3}, {"一起饭": "X"}, "2026-01-01T00:00:00-08:00")
    _excel(df, monkeypatch, n0={2: "0l"})          # 1n+ row came back blank
    data = df.refresh_headers()
    assert "0l" in data["0n"] and "一起饭" in data["1n"]


def test_empty_cache_marked_fresh_is_refreshed_once_it_is_old_enough(df, monkeypatch):
    # The file this bug left behind: fresh stamp, no habits.
    now = df._daytime.local_now().isoformat()
    _cache(df, {}, {}, now)
    calls = _excel(df, monkeypatch, *GOOD)
    import os
    old = time.time() - 600
    os.utime(df.HEADERS_PATH, (old, old))
    assert "一起饭" in df.load_headers()["1n"]
    assert calls == READS                       # one refresh: one read per sheet


def test_empty_cache_is_not_retried_on_every_call(df, monkeypatch):
    _cache(df, {}, {}, df._daytime.local_now().isoformat())
    calls = _excel(df, monkeypatch)
    df.load_headers()
    df.load_headers()
    assert calls == []


# --- 2026-11-25: row 1 read through excel-http, same parse as the old script ---

def test_refresh_maps_0n_to_column_numbers_and_1n_to_letters(df, monkeypatch):
    calls = _excel(df, monkeypatch,
                   n0={0: "mee", 2: "2026.0", 5: "wake up ", 18: "0l", 40: "NVC", 61: "last"},
                   n1={0: "1s", 1: "1g ", 19: "长o314", 24: "长冥想", 35: "1 kids nature ", 37: "avg"})
    data = df.refresh_headers()
    assert calls == READS
    # 1-based column numbers; trailing blanks trimmed; lower-cased
    assert data["0n"] == {"mee": 1, "2026.0": 3, "wake up": 6, "0l": 19, "nvc": 41, "last": 62}
    # C is the first column of the 1n+ range
    assert data["1n"] == {"1s": "C", "1g": "D", "长o314": "V", "长冥想": "AA",
                          "1 kids nature": "AL", "avg": "AN"}
    assert json.loads(df.HEADERS_PATH.read_text())["0n"]["0l"] == 19


def test_refresh_header_text_edge_forms(df, monkeypatch):
    """What the old script's output parser kept: the first line of a
    multi-line header, trailing whitespace (incl. no-break space) dropped,
    leading whitespace kept, whitespace-only cells skipped."""
    _excel(df, monkeypatch,
           n0={3: "two\nlines", 4: "cr\rline", 5: "nbsp ", 6: " lead", 7: "   ", 8: "\nonly second"},
           n1={0: "1s"})
    assert df.refresh_headers()["0n"] == {"two": 4, "cr": 5, "nbsp": 6, " lead": 7}


@pytest.mark.parametrize("fail", ["0n", "1n+"])
def test_refresh_failure_exits_3_and_leaves_the_cache(df, monkeypatch, capsys, fail):
    """A refused or failed daemon read takes the path a failed osascript took."""
    _cache(df, {"0l": 3}, {"一起饭": "X"}, "2026-01-01T00:00:00-08:00")
    before = df.HEADERS_PATH.read_text()
    _excel(df, monkeypatch, *GOOD, fail=fail)
    with pytest.raises(SystemExit) as e:
        df.refresh_headers()
    assert e.value.code == 3
    assert "refresh_headers failed" in capsys.readouterr().err
    assert df.HEADERS_PATH.read_text() == before


def test_refresh_client_exception_exits_3(df, monkeypatch):
    def boom(*a, **k):
        raise OSError("ssh died")
    monkeypatch.setattr(df.neon_excel, "read_range", boom)
    with pytest.raises(SystemExit) as e:
        df.refresh_headers()
    assert e.value.code == 3


def test_refresh_short_row_still_maps_from_the_first_column(df, monkeypatch):
    """Cells are indexed from the range's first column, so a reply that stops
    early (trailing blanks dropped) maps what it has; no rows at all fails."""
    monkeypatch.setattr(df.neon_excel, "read_range",
                        lambda sheet, rng: {"ok": True, "rows": [["a", "", "B "]]})
    data = df.refresh_headers()
    assert data["0n"] == {"a": 1, "b": 3} and data["1n"] == {"a": "C", "b": "E"}
    monkeypatch.setattr(df.neon_excel, "read_range", lambda sheet, rng: {"ok": True, "rows": []})
    with pytest.raises(SystemExit) as e:
        df.refresh_headers()
    assert e.value.code == 3


def test_refresh_headers_has_no_raw_applescript():
    src = (HERE / "did-fast.py").read_text()
    body = src[src.index("def refresh_headers("):src.index("# Task queue cache")]
    assert "tell application" not in body and "ix_run(" not in body
