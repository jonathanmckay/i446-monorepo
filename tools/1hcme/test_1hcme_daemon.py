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
