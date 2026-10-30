"""1i9 → i9+m5x2 B/D/E/F through the daemon (raw ix-osa only when it is down)."""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture(scope="module")
def m():
    spec = importlib.util.spec_from_file_location("one_i9_daemon", HERE / "1i9.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["one_i9_daemon"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_write_row_uses_daemon(m, monkeypatch):
    seen = {}

    def fake_bw(sheet, items, *, date=None, row=None, src=None, fallback=True):
        seen.update(sheet=sheet, items=items, date=date, row=row, src=src, fallback=fallback)
        return {"ok": True, "row": row, "results": items}
    monkeypatch.setattr(m.neon_excel, "batch_write", fake_bw)
    monkeypatch.setattr(m, "_osa", lambda *a, **k: pytest.fail("raw path must not run"))
    assert m.write_row(44, "MA", "shipped X\nmet Y", "=ship Z", 2134, label="10.3") == \
        "OK: i9+m5x2!row 44"
    assert seen["sheet"] == "i9+m5x2" and seen["row"] == 44 and seen["date"] == "10.3"
    assert seen["fallback"] is False and seen["src"] == "1i9"
    assert seen["items"] == [
        {"col": "B", "value": "MA", "mode": "text"},
        {"col": "D", "value": "shipped X\nmet Y", "mode": "text"},
        {"col": "E", "value": "=ship Z", "mode": "text"},   # prose, never a formula
        {"col": "F", "value": 2134},
    ]
    assert "C" not in {it["col"] for it in seen["items"]}  # C is a formula into 1分+1s


def test_write_row_falls_back_when_daemon_down(m, monkeypatch):
    monkeypatch.setattr(m.neon_excel, "batch_write",
                        lambda *a, **k: {"ok": False, "error": "daemon_unreachable", "fallback": True})
    calls = []
    monkeypatch.setattr(m, "_osa", lambda script, timeout=120: calls.append(script) or "OK")
    assert m.write_row(44, "MA", "d", "e", 5) == "OK"
    assert calls and "set value of cell 6 of row 44" in calls[0]


def test_write_row_daemon_error_is_not_masked(m, monkeypatch):
    monkeypatch.setattr(m.neon_excel, "batch_write",
                        lambda *a, **k: {"ok": False, "error": "echo_mismatch:1/4"})
    monkeypatch.setattr(m, "_osa", lambda *a, **k: pytest.fail("no raw retry on a daemon error"))
    with pytest.raises(RuntimeError, match="echo_mismatch"):
        m.write_row(44, "MA", "d", "e", 5)
