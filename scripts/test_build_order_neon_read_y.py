"""neon_read_y reads 0分!P through the excel-http client.

2026-11-25: it was the last raw AppleScript READ in build-order-daemon.py
(NEON_FIND_ROW_TEMPLATE + osascript). Its callers compare the result to
"ERROR" / "" / "0" for the post-write read-back (neon_add_score_to_p,
reconcile_p) and print it as the day's rating in the archive email, so the
contract is: the cell's value as text, or exactly "ERROR"; never an exception.
"""
import datetime as dt
import importlib.util
import inspect
import subprocess
from pathlib import Path

import pytest

DAEMON = Path(__file__).parent / "build-order-daemon.py"


@pytest.fixture()
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("build_order_daemon_ready", DAEMON)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    logs = []
    monkeypatch.setattr(m, "log", logs.append)
    m._logs = logs
    return m


def _fake_read(monkeypatch, mod, reply):
    calls = []

    def read(sheet, col, *, date=None, row=None):
        calls.append((sheet, col, date, row))
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(mod.neon_excel, "read", read)
    return calls


def test_returns_value_text_and_asks_for_the_date_row(monkeypatch, mod):
    calls = _fake_read(monkeypatch, mod, {"ok": True, "row": 327, "col": "P",
                                          "value": "14.0", "formula": "=4+10"})
    assert mod.neon_read_y(dt.date(2026, 11, 25)) == "14.0"
    assert calls == [("0分", "P", "11/25", None)]


def test_single_digit_month_and_day_are_not_padded(monkeypatch, mod):
    calls = _fake_read(monkeypatch, mod, {"ok": True, "value": "0.0", "formula": ""})
    assert mod.neon_read_y(dt.date(2026, 1, 5)) == "0.0"
    assert calls[0][2] == "1/5"


@pytest.mark.parametrize("value,expected", [("", ""), (" 81.0\n", "81.0"), (None, "")])
def test_blank_and_padded_values(monkeypatch, mod, value, expected):
    _fake_read(monkeypatch, mod, {"ok": True, "value": value, "formula": ""})
    assert mod.neon_read_y(dt.date(2026, 11, 25)) == expected


@pytest.mark.parametrize("reply", [
    {"ok": False, "error": "date_not_found_or_missing_target"},
    {"ok": False, "error": "queue_pending"},
    {"ok": False, "error": "date_not_found_fallback", "fallback": True},
    {},
    None,
])
def test_failed_read_is_ERROR_and_logged(monkeypatch, mod, reply):
    _fake_read(monkeypatch, mod, reply)
    assert mod.neon_read_y(dt.date(2026, 11, 25)) == "ERROR"
    assert any(line.startswith("read Y: FAILED") for line in mod._logs)


@pytest.mark.parametrize("exc", [
    subprocess.TimeoutExpired("ssh", 45), FileNotFoundError("ssh"), RuntimeError("boom")])
def test_exception_is_ERROR_never_raised(monkeypatch, mod, exc):
    # run_archive calls neon_read_y outside any try: it must not raise.
    _fake_read(monkeypatch, mod, exc)
    assert mod.neon_read_y(dt.date(2026, 11, 25)) == "ERROR"
    assert any(line.startswith("read Y: ERROR") for line in mod._logs)


def test_unmocked_client_under_pytest_is_ERROR_not_a_live_read(mod):
    # neon.excel refuses the live workbook under pytest; that surfaces as ERROR.
    assert mod.neon_read_y(dt.date(2026, 11, 25)) == "ERROR"


def test_readback_callers_treat_ERROR_as_verify_failed(monkeypatch, mod):
    monkeypatch.setattr(mod.neon_excel, "append",
                        lambda *a, **k: {"ok": True, "formula": "=4+10"})
    _fake_read(monkeypatch, mod, {"ok": False, "error": "excel_error"})
    assert mod.neon_add_score_to_p(dt.date(2026, 11, 25), 10) == "VERIFY_FAILED"
    _fake_read(monkeypatch, mod, {"ok": True, "value": "14.0", "formula": "=4+10"})
    assert mod.neon_add_score_to_p(dt.date(2026, 11, 25), 10) == "P_APPEND =4+10"


def test_no_applescript_in_neon_read_y(mod):
    src = inspect.getsource(mod.neon_read_y)
    for raw in ("_osascript", "osascript", "NEON_FIND_ROW_TEMPLATE", "subprocess"):
        assert raw not in src
    assert "neon_excel.read(" in src
