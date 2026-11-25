"""Regression (2026-11-25): with excel-http unreachable, a 1n+ write was
applied twice. write_1n_via_daemon called neon.excel.batch_write with its
default fallback, so the CLIENT appended each cell over ssh+osascript and
returned fallback=True; did-fast read that as "daemon unavailable" and the
caller (did-fast step 4b, 1n-backfill step 1) then ran build_1n_script, which
appended the same minutes again. The 0n twin had the same two writers (its
"set" cells were written by both; "add" cells only by the raw script).

The fix passes fallback=False: the client writes nothing when the daemon is
down, so the raw script is the only writer. These tests run the REAL
neon.excel.batch_write with its transport faked (daemon refused) and count
what reaches the cell."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def df():
    return _load("did_fast_single_writer", "did-fast.py")


@pytest.fixture
def daemon_down(df, monkeypatch):
    """The real client with the daemon refusing connections. Returns the
    list of cells the client's own ssh fallback wrote (must stay empty)."""
    ex = df.neon_excel
    client_writes: list = []

    def refused(path, body):
        return None, False            # _send_write: nothing was sent

    def ssh_fallback(op, sheet, col, date, row, value, src=None, idem=None):
        client_writes.append((op, sheet, col, value))
        return {"ok": True, "row": row or 51, "col": col, "value": "1", "formula": "=1",
                "fallback": True}

    monkeypatch.setattr(ex, "_send_write", refused)
    monkeypatch.setattr(ex, "_ssh_fallback", ssh_fallback)
    monkeypatch.setattr(ex, "_surface", lambda: {})
    return client_writes


def _w1n(df, name, col, value=1, cumulative=0, variable=False):
    return df.RouteResult(item=SimpleNamespace(name=name), step="1n", col_letter=col,
                          write_value=value, is_cumulative_1n=bool(cumulative),
                          cumulative_increment=cumulative, is_variable_1n=variable)


def _w0n(df, name, col_num, value=1):
    return df.RouteResult(item=SimpleNamespace(name=name), step="0n", col_num=col_num,
                          write_value=value)


def test_1n_daemon_down_client_writes_nothing(df, daemon_down):
    writes = [_w1n(df, "family", "AF", value=30, variable=True),      # append
              _w1n(df, "问学 wk", "S", cumulative=20),                 # append
              _w1n(df, "1 cal", "R")]                                  # set
    assert df.write_1n_via_daemon(writes, "11.3") is None             # → caller runs the raw script
    assert daemon_down == [], "the client appended on its own; the raw script would append again"


def test_0n_daemon_down_client_writes_nothing(df, daemon_down):
    writes = [_w0n(df, "hiit", 31), _w0n(df, "xk20", 36, 15)]         # set + add
    assert df.write_0n_via_daemon(writes, "11/24") is None
    assert daemon_down == []


@pytest.mark.parametrize("fn,writes,target", [
    ("write_1n_via_daemon", lambda df: [_w1n(df, "family", "AF", value=30, variable=True)], "11.3"),
    ("write_0n_via_daemon", lambda df: [_w0n(df, "hiit", 31)], "11/24"),
])
def test_batch_write_is_called_with_fallback_off(df, monkeypatch, fn, writes, target):
    seen = {}

    def bw(sheet, items, **kw):
        seen.update(kw)
        return {"ok": False, "error": "daemon_unreachable", "fallback": True}
    monkeypatch.setattr(df.neon_excel, "batch_write", bw)
    assert getattr(df, fn)(writes(df), target) is None
    assert seen.get("fallback") is False


def test_1n_backfill_daemon_down_appends_the_minutes_once(daemon_down, monkeypatch, capsys):
    """End to end through 1n-backfill main(): one application to the week cell."""
    ob = _load("on_backfill_single_writer", "1n-backfill.py")
    dfm = ob._did()
    ex = dfm.neon_excel
    assert ex is ob.neon_excel
    monkeypatch.setattr(dfm, "load_headers", lambda: {"0n": {}, "1n": {"family": "AF"}})
    monkeypatch.setattr(ob, "_did", lambda: dfm)
    monkeypatch.setattr(ob.daytime, "today", lambda: date(2026, 11, 25))
    monkeypatch.setattr(ob, "read_week_cell",
                        lambda col, week: {"ROW": "51", "CELL": "", "E": "1/m"})
    monkeypatch.setattr(ob, "_load_0n_backfill",
                        lambda: SimpleNamespace(save_and_refresh=lambda: "ok"))
    credits = []
    monkeypatch.setattr(ex, "batch_append",
                        lambda sheet, appends, **kw: credits.append((sheet, appends, kw))
                        or {"ok": True, "row": 327})

    cell: list = []          # every "+30" that reaches 1n+!AF51, whoever wrote it

    def raw(script, timeout=30.0):
        assert 'set formula of theCell1n to oldFormula1n & "+30"' in script
        cell.append("raw script")
        return subprocess.CompletedProcess(["osascript"], 0,
                                           stdout="OK:weekRow=51|AF=30|\nPRE\tAF\t\n", stderr="")
    monkeypatch.setattr(ob, "ix_run", raw)
    monkeypatch.setattr(sys, "argv", ["1n-backfill.py", "family", "11.3", "30"])

    assert ob.main() == 0
    cell += [f"client ssh fallback {c}" for c in daemon_down]
    assert cell == ["raw script"], f"week cell written {len(cell)} times: {cell}"
    assert len(credits) == 1                       # and today's 0分 credit once
    assert '"cell_written": "OK:weekRow=51|AF=30|"' in capsys.readouterr().out
