"""/1hcmc's answer reaches 1分+1s!AO through the daemon, so the ledger sees it."""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("hcmc_survey_w", HERE / "hcmc-survey.py")
hs = importlib.util.module_from_spec(spec)
sys.modules["hcmc_survey_w"] = hs
spec.loader.exec_module(hs)


@pytest.fixture()
def wire(monkeypatch):
    calls, raw = [], []

    def install(*replies):
        def fake(sheet, items, **kw):
            calls.append((sheet, items, kw))
            return replies[min(len(calls) - 1, len(replies) - 1)]
        monkeypatch.setattr(hs.neon_excel, "batch_write", fake)
        monkeypatch.setattr(hs, "osa", lambda s: raw.append(s) or "RAW")
        return calls, raw
    return install


def test_writes_text_to_the_displayed_row(wire):
    calls, raw = wire({"ok": True})
    assert hs.write_answer("10.3", 44, "• a") == "OK: 1分+1s!AO44"
    sheet, items, kw = calls[0]
    assert sheet == "1分+1s"
    assert items == [{"col": "AO", "value": "• a", "mode": "text"}]
    assert (kw["row"], kw["date"], kw["fallback"]) == (44, "10.3", False)
    assert raw == []


def test_stalled_excel_retries_once(wire):
    calls, raw = wire({"ok": False, "error": "osascript_timeout"}, {"ok": True})
    assert hs.write_answer("10.3", 44, "• a").startswith("OK")
    assert len(calls) == 2 and raw == []


def test_failure_prints_the_answer_and_skips_raw(wire):
    _, raw = wire({"ok": False, "error": "excel_error"})
    with pytest.raises(SystemExit, match="• keep me"):
        hs.write_answer("10.3", 44, "• keep me")
    assert raw == []


def test_daemon_down_uses_raw(wire):
    _, raw = wire({"ok": False, "error": "daemon_unreachable", "fallback": True})
    assert hs.write_answer("10.3", 44, "• a") == "RAW"
    assert 'range "AO44"' in raw[0]


def test_test_guard_error_never_reaches_raw(monkeypatch):
    def guard(*a, **k):
        raise RuntimeError("neon client called from a test")
    monkeypatch.setattr(hs.neon_excel, "batch_write", guard)
    monkeypatch.setattr(hs, "osa", lambda s: pytest.fail("raw ix-osa must not run"))
    with pytest.raises(RuntimeError):
        hs.write_answer("10.3", 44, "• a")
