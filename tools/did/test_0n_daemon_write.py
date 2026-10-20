"""Neon-off-Excel step 2: did-fast's 0n habit writes go through the daemon
(/batch_write) and synthesize the raw script's echo so verify_0n_output and
the ctrl-z pre-image parser keep working; daemon-down falls back to ix-osa."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent


@pytest.fixture(scope="module")
def df():
    spec = importlib.util.spec_from_file_location("did_fast_0n_daemon", HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_0n_daemon"] = mod
    spec.loader.exec_module(mod)
    return mod


def _w(df, name, col_num, value=1):
    return df.RouteResult(item=SimpleNamespace(name=name), step="0n", col_num=col_num,
                         write_value=value)


def test_daemon_path_synthesizes_echo_and_pre_images(df, monkeypatch):
    seen = {}

    def fake_batch_write(sheet, writes, *, date=None, row=None, src=None):
        seen.update(sheet=sheet, writes=writes, date=date, src=src)
        return {"ok": True, "row": 293, "results": [
            {"ok": True, "col": "AE", "value": "1", "formula": "1", "chain": "ok",
             "before_value": "", "mode": "set"},
            {"ok": True, "col": "AJ", "value": "45", "formula": "45", "chain": "ok",
             "before_value": "30", "mode": "add"},
        ]}

    monkeypatch.setattr(df.neon_excel, "batch_write", fake_batch_write)
    writes = [_w(df, "hiit", 31), _w(df, "xk20", 36, 15)]
    res = df.write_0n_via_daemon(writes, "10/19")

    assert seen["sheet"] == "0n" and seen["date"] == "10/19"
    assert seen["writes"] == [{"col": "AE", "value": 1, "mode": "set"},
                              {"col": "AJ", "value": 15, "mode": "add"}]   # xk20 is cumulative
    assert seen["src"] == "did hiit, xk20"
    assert res.returncode == 0
    assert df.verify_0n_output(res.stdout, writes) is None                # echo satisfies verify
    assert df.parse_pre_lines(res.stdout) == {"31": "", "36": "30"}       # ctrl-z pre-images


@pytest.mark.parametrize("resp", [
    None,
    {"ok": False, "error": "daemon_unreachable", "fallback": True},
    {"ok": False, "error": "date_not_found_or_missing_target"},
    {"ok": True, "row": 293, "results": []},                               # echo mismatch
])
def test_daemon_unavailable_returns_none_for_raw_fallback(df, monkeypatch, resp):
    monkeypatch.setattr(df.neon_excel, "batch_write", lambda *a, **k: resp)
    assert df.write_0n_via_daemon([_w(df, "hiit", 31)], "10/19") is None


def test_daemon_exception_returns_none(df, monkeypatch):
    def boom(*a, **k):
        raise OSError("ssh died")
    monkeypatch.setattr(df.neon_excel, "batch_write", boom)
    assert df.write_0n_via_daemon([_w(df, "hiit", 31)], "10/19") is None


def test_step4_calls_daemon_before_raw_script():
    src = (HERE / "did-fast.py").read_text()
    i_daemon = src.index("on_result = write_0n_via_daemon(on_writes, target_date)")
    i_raw = src.index("script = build_0n_script(on_writes, target_date)", i_daemon)
    assert i_daemon < i_raw, "daemon must be tried first; raw ix-osa is the fallback"
    assert "if on_result is None:" in src[i_daemon:i_raw]


def test_0l_time_stamp_goes_through_batch_write_as_number():
    src = (HERE / "did-fast.py").read_text()
    seg = src[src.index("# 4a-ii. 0l special case"):src.index("# 4b. Batch 1n+ writes")]
    assert "neon_excel.batch_write(" in seg
    assert '"value": _ol_now.hour * 100 + _ol_now.minute}' in seg, "HHMM must be a number, not str()"
    assert "build_0l_time_script(target_date)" in seg, "raw script stays as the fallback"
