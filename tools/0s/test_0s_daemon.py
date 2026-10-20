"""0s → 0s897 through the excel-http daemon (Neon off Excel step 2c)."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("zeros_daemon", Path(__file__).parent / "0s.py")
zs = importlib.util.module_from_spec(_spec)
sys.modules["zeros_daemon"] = zs
_spec.loader.exec_module(zs)


def _pick(kind, target):
    return next((k, c) for k, _l, c, t, kd in zs.FIELDS if c and kd == kind and t == target)


def test_daemon_writes_split_rows_and_types():
    (tkey, tcol) = _pick("text", "today")
    (nkey, ncol) = _pick("num", "today")
    (mkey, mcol) = _pick("text", "tomorrow")
    other_text = next(k for k, _l, c, t, kd in zs.FIELDS if c and kd == "text" and t == "today" and k != tkey)
    answers = {tkey: "Good day", other_text: "", nkey: "7", mkey: "go\nhard"}
    today, tomorrow = zs.daemon_writes(answers)
    assert {"col": tcol, "value": "Good day", "mode": "text"} in today
    assert {"col": ncol, "value": 7.0} in today                                 # number stays a number
    assert tomorrow == [{"col": mcol, "value": "go\nhard", "mode": "text"}]     # next-day row
    assert len(today) == 2                                                      # blanks never written


def test_write_answers_prefers_daemon_with_mdy_dates(monkeypatch):
    calls = []

    def fake_bw(sheet, items, *, date=None, row=None, src=None, fallback=True):
        calls.append((sheet, date, fallback, [i["col"] for i in items]))
        return {"ok": True, "row": 290 if date == "10/18/26" else 291, "results": items}
    monkeypatch.setattr(zs.neon_excel, "batch_write", fake_bw)
    monkeypatch.setattr(zs, "run_bounded", lambda *a, **k: pytest.fail("raw path must not run"))
    out = zs.write_answers({_pick("text", "today")[0]: "t", _pick("text", "tomorrow")[0]: "m"}, dt.date(2026, 10, 18))
    assert out == "OK: todayRow=290 tomRow=291 wrote=2"
    assert [c[1] for c in calls] == ["10/18/26", "10/19/26"] and all(c[2] is False for c in calls)


def test_missing_next_day_row_is_not_fatal(monkeypatch):
    def fake_bw(sheet, items, *, date=None, row=None, src=None, fallback=True):
        if date == "1/1/27":
            return {"ok": False, "error": "date_not_found_or_missing_target"}
        return {"ok": True, "row": 377, "results": items}
    monkeypatch.setattr(zs.neon_excel, "batch_write", fake_bw)
    out = zs.write_answers({_pick("text", "today")[0]: "t", _pick("text", "tomorrow")[0]: "m"}, dt.date(2026, 12, 31))
    assert out == "OK: todayRow=377 tomRow=0 wrote=1"


def test_daemon_down_falls_back_to_raw_script(monkeypatch):
    monkeypatch.setattr(zs.neon_excel, "batch_write",
                        lambda *a, **k: {"ok": False, "error": "daemon_unreachable", "fallback": True})

    class P:
        returncode = 0
        stdout = "OK: todayRow=290 tomRow=291 wrote=1"
        stderr = ""
    ran = []
    monkeypatch.setattr(zs, "run_bounded", lambda *a, **k: (ran.append(a), P())[1])
    assert zs.write_answers({_pick("text", "today")[0]: "t"}, dt.date(2026, 10, 18)).startswith("OK: todayRow=290")
    assert len(ran) == 1


def test_daemon_logical_error_raises(monkeypatch):
    monkeypatch.setattr(zs.neon_excel, "batch_write",
                        lambda *a, **k: {"ok": False, "error": "osascript_timeout"})
    with pytest.raises(RuntimeError, match="osascript_timeout"):
        zs.write_answers({_pick("text", "today")[0]: "t"}, dt.date(2026, 10, 18))
