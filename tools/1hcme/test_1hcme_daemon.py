"""1hcme → 0s897!X through the daemon (text mode, row from the fetch)."""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture(scope="module")
def hm():
    spec = importlib.util.spec_from_file_location("one_hcme_daemon", HERE / "1hcme.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["one_hcme_daemon"] = m
    spec.loader.exec_module(m)
    return m


def test_write_answer_uses_daemon_text_mode(hm, monkeypatch):
    seen = {}

    def fake_bw(sheet, items, *, date=None, row=None, src=None, fallback=True):
        seen.update(sheet=sheet, items=items, row=row, fallback=fallback)
        return {"ok": True, "row": row, "results": items}
    monkeypatch.setattr(hm.neon_excel, "batch_write", fake_bw)
    monkeypatch.setattr(hm, "run_bounded", lambda *a, **k: pytest.fail("raw path must not run"))
    assert hm.write_answer(300, "• calm\n• tired") == "OK: %s!%s300" % (hm.SHEET, hm.HCME_COL)
    assert seen["sheet"] == "0s897" and seen["row"] == 300 and seen["fallback"] is False
    assert seen["items"] == [{"col": hm.HCME_COL, "value": "• calm\n• tired", "mode": "text"}]


def test_write_answer_falls_back_when_daemon_down(hm, monkeypatch):
    monkeypatch.setattr(hm.neon_excel, "batch_write",
                        lambda *a, **k: {"ok": False, "error": "daemon_unreachable", "fallback": True})

    class P:
        returncode = 0
        stdout = "OK: 0s897!X300"
        stderr = ""
    monkeypatch.setattr(hm, "run_bounded", lambda *a, **k: P())
    assert hm.write_answer(300, "x") == "OK: 0s897!X300"


def test_window_is_thursday_through_wednesday(hm):
    import datetime as dt
    target = dt.date(2026, 10, 22)  # Thursday
    days = hm.window(target, dt.date(2026, 10, 24))
    assert days[0] == dt.date(2026, 10, 15) and days[0].weekday() == 3
    assert days[-1] == dt.date(2026, 10, 21) and days[-1].weekday() == 2


def test_checkin_row_is_the_thursday_that_starts_the_reviewed_week(hm, monkeypatch):
    # 2026-11-05: "should be the last 7 days, not the next 7". The check-in
    # reviewing Th 10/29 - We 11/4 belongs on the 10/29 row (11.1), not on
    # 11/5, whose week is the next 7 days.
    import datetime as dt
    seen = []

    def fake_fetch(dates):
        seen.extend(dates)
        return {i: {"row": d.toordinal(), "cells": {}} for i, d in enumerate(dates)}
    monkeypatch.setattr(hm, "fetch", fake_fetch)
    target, today = dt.date(2026, 11, 5), dt.date(2026, 11, 5)
    dates, recs, prev, trec = hm.load(target, today)
    assert dates[0] == dt.date(2026, 10, 29) and dates[-1] == dt.date(2026, 11, 4)
    assert trec["row"] == dt.date(2026, 10, 29).toordinal()
    assert prev["row"] == dt.date(2026, 10, 22).toordinal()
    assert target not in seen
    assert len(recs) == 7 and recs[6]["row"] == dt.date(2026, 11, 4).toordinal()


def test_week_label_names_the_week_under_review(hm, monkeypatch):
    import datetime as dt
    monkeypatch.setattr(hm, "thursday_for_label", lambda label: dt.date(2026, 10, 29))
    assert hm.resolve_target("11.1", dt.date(2026, 11, 5)) == dt.date(2026, 11, 5)


def _answer(hm, keys):
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    with create_pipe_input() as inp:
        inp.send_text(keys)
        with create_app_session(input=inp, output=DummyOutput()):
            return hm.read_answer()


def test_enter_on_empty_line_saves(hm):
    assert _answer(hm, "one\rtwo\r\r") == "one\ntwo"


def test_ctrl_s_saves(hm):
    assert _answer(hm, "one\rtwo\x13") == "one\ntwo"


def test_ctrl_c_aborts(hm):
    assert _answer(hm, "one\x03") == ""


# ── 2026-11-03: 0s897!B display moved m/d/yy -> yyyy.mm.dd ──────────────────
# Both col-B lookups key on the date VALUE (y-m-d), never the displayed text.

def test_fetch_and_label_scripts_match_date_value_not_display(hm):
    import datetime as dt
    f = hm.build_fetch_script([dt.date(2026, 10, 29)])
    assert 'set tmpB to value of range "B2:B600"' in f and "10/29/26" not in f
    assert '((class of cv) as text) is "date"' in f and 'if bv = "2026-10-29"' in f
    lab = hm.build_label_script("11.1")
    assert 'set tmpB to value of range "B2:B600"' in lab
    assert 'return "OK|" & ((year of cv) as text)' in lab


def test_thursday_for_label_parses_ymd_key(hm, monkeypatch):
    import datetime as dt
    import types
    monkeypatch.setattr(hm, "run_bounded", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="OK|2026-10-29\n", stderr=""))
    assert hm.thursday_for_label("11.1") == dt.date(2026, 10, 29)
