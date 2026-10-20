"""book-finish → 1分+1s!AM +1 through the daemon's atomic add mode (step 2c)."""
import datetime
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("book_finish_daemon", os.path.join(HERE, "book-finish.py"))
bf = importlib.util.module_from_spec(spec)
sys.modules["book_finish_daemon"] = bf
spec.loader.exec_module(bf)


def test_bump_uses_daemon_add_mode_by_week_label(monkeypatch):
    seen = {}

    def fake_bw(sheet, items, *, date=None, row=None, src=None, fallback=True):
        seen.update(sheet=sheet, items=items, date=date, fallback=fallback)
        return {"ok": True, "row": 44, "results": [{"ok": True, "col": "AM", "value": "3"}]}
    monkeypatch.setattr(bf.neon_excel, "batch_write", fake_bw)
    monkeypatch.setattr(bf, "run_ix_osa", lambda s: (_ for _ in ()).throw(AssertionError("raw path ran")))
    ok, msg = bf.bump_books_read(datetime.date(2026, 10, 19))
    assert ok and msg == "OK: AM44=3"
    assert seen["sheet"] == "1分+1s" and seen["date"] == "10.3" and seen["fallback"] is False
    assert seen["items"] == [{"col": "AM", "value": 1, "mode": "add"}]


def test_bump_falls_back_to_raw_when_daemon_down(monkeypatch):
    monkeypatch.setattr(bf.neon_excel, "batch_write",
                        lambda *a, **k: {"ok": False, "error": "daemon_unreachable", "fallback": True})
    monkeypatch.setattr(bf, "run_ix_osa", lambda s: (True, "OK: AM44=3"))
    assert bf.bump_books_read(datetime.date(2026, 10, 19)) == (True, "OK: AM44=3")


def test_bump_reports_logical_daemon_error(monkeypatch):
    monkeypatch.setattr(bf.neon_excel, "batch_write", lambda *a, **k: {"ok": False, "error": "date_not_found_or_missing_target"})
    ok, msg = bf.bump_books_read(datetime.date(2026, 10, 19))
    assert not ok and "date_not_found" in msg
