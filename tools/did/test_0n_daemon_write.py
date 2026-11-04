"""Neon-off-Excel step 2: did-fast's 0n habit writes go through the daemon
(/batch_write) and synthesize the raw script's echo so verify_0n_output and
the ctrl-z pre-image parser keep working; daemon-down falls back to ix-osa."""
from __future__ import annotations

from datetime import date
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

    def fake_batch_write(sheet, writes, *, date=None, row=None, src=None, fallback=True):
        seen.update(sheet=sheet, writes=writes, date=date, src=src, fallback=fallback)
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
    assert seen["fallback"] is False   # no ssh per-cell writes under the raw-script fallback
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


# ---------------------------------------------------------------------------
# Step 2b: 1n+ via /batch_write, hcbi via /batch
# ---------------------------------------------------------------------------

def _w1n(df, name, col, value=1, cumulative=0, variable=False):
    return df.RouteResult(item=SimpleNamespace(name=name), step="1n", col_letter=col,
                          write_value=value, is_cumulative_1n=bool(cumulative),
                          cumulative_increment=cumulative, is_variable_1n=variable)


def test_1n_daemon_path_maps_modes_and_synthesizes_echo(df, monkeypatch):
    seen = {}

    def fake_bw(sheet, writes, *, date=None, row=None, src=None, fallback=True):
        seen.update(sheet=sheet, writes=writes, date=date, src=src, fallback=fallback)
        return {"ok": True, "row": 20, "results": [
            {"ok": True, "col": "D", "value": "75", "formula": "=0+30+45", "before": "=0+30",
             "before_value": "30", "mode": "append", "chain": "ok"},
            {"ok": True, "col": "K", "value": "1", "formula": "1", "before": "", "before_value": "1",
             "mode": "set", "chain": "ok"},
            {"ok": True, "col": "S", "value": "20", "formula": "=20", "before": "", "before_value": "",
             "mode": "append", "chain": "ok"},
        ]}
    monkeypatch.setattr(df.neon_excel, "batch_write", fake_bw)
    writes = [_w1n(df, "family", "D", value=45, variable=True),
              _w1n(df, "1 cal", "K", value=1),
              _w1n(df, "问学 wk", "S", cumulative=20)]
    res = df.write_1n_via_daemon(writes, "10.3")
    assert seen["sheet"] == "1n+" and seen["date"] == "10.3"
    assert seen["fallback"] is False   # appends: ssh fallback + raw script = double credit
    assert seen["writes"] == [{"col": "D", "value": "+45", "mode": "append"},
                              {"col": "K", "value": 1, "mode": "set"},
                              {"col": "S", "value": "+20", "mode": "append"}]
    assert res.returncode == 0 and res.stdout.startswith("OK:weekRow=20|D=75|K=1|S=20|")
    import re
    assert re.search(r"weekRow=(\d+)", res.stdout).group(1) == "20"       # step 4c's regex
    # PRE restore rule: formula if present, else numeric value (a plain-number
    # cell can echo formula "" with value "1"), else blank
    assert df.parse_pre_lines(res.stdout) == {"D": "=0+30", "K": "1", "S": ""}


@pytest.mark.parametrize("r,expected", [
    ({"before": "=0+30", "before_value": "30"}, "=0+30"),
    ({"before": "", "before_value": "1"}, "1"),
    ({"before": "1", "before_value": "1"}, "1"),
    ({"before": "", "before_value": ""}, ""),
    ({"before": "", "before_value": "missing value"}, ""),
    ({}, ""),
])
def test_pre_image_text_restore_rule(df, r, expected):
    assert df._pre_image_text(r) == expected


def test_1n_daemon_unavailable_returns_none(df, monkeypatch):
    monkeypatch.setattr(df.neon_excel, "batch_write",
                        lambda *a, **k: {"ok": False, "error": "date_not_found_or_missing_target"})
    assert df.write_1n_via_daemon([_w1n(df, "1 cal", "K")], "10.3") is None


def test_hcbi_via_daemon_batch_with_idem(df, monkeypatch):
    seen = {}

    def fake_ba(sheet, items, *, date=None, row=None, src=None):
        seen.update(sheet=sheet, items=items, date=date, src=src)
        return {"ok": True, "row": 197, "results": [{"ok": True, "col": "I", "value": "2", "chain": "ok"}]}
    monkeypatch.setattr(df.neon_excel, "batch_append", fake_ba)
    res = df.append_hcbi_via_daemon([{"col": "I", "value": "+1", "idem": "hcbi:T1@10/19:I"},
                                     {"col": "Y", "value": "+15", "idem": None}], "10/19", src="did hiit")
    assert seen["sheet"] == "hcbi" and seen["date"] == "10/19" and seen["src"] == "did hiit"
    assert seen["items"] == [{"col": "I", "value": "+1", "idem": "hcbi:T1@10/19:I"},
                             {"col": "Y", "value": "+15"}]                 # None idem dropped
    assert res.returncode == 0 and res.stdout == "OK:hcbi row=197"


def test_hcbi_daemon_failure_is_rc1_with_stderr(df, monkeypatch):
    monkeypatch.setattr(df.neon_excel, "batch_append",
                        lambda *a, **k: {"ok": False, "results": [{"ok": False, "error": "osascript_timeout"}]})
    res = df.append_hcbi_via_daemon([{"col": "I", "value": "+1"}], "10/19", src="did")
    assert res.returncode == 1 and "osascript_timeout" in res.stderr


def test_step4b_calls_daemon_before_raw_1n_script():
    src = (HERE / "did-fast.py").read_text()
    i_daemon = src.index("one_n_result = write_1n_via_daemon(group, week_mw)")
    i_raw = src.index("script = build_1n_script(group, week_mw)", i_daemon)
    assert "if one_n_result is None:" in src[i_daemon:i_raw]
    assert "build_hcbi_script" not in src.replace("# build_hcbi_script AppleScript", "")


# ── step 4b per-batch rows (2026-10-27 review) ──────────────────────────────
def test_1n_batches_keep_row_pre_and_success_per_week(df, monkeypatch):
    """A Sunday `/did 1 -2g, 1 i9` writes two weeks (10.3 for the Monday-cycle
    card, 10.4 for the rest). Each entry must carry ITS batch's row and PRE
    map, and a failed batch must not count as written (no 0分 credit)."""
    from types import SimpleNamespace as NS
    a = _w1n(df, "1 -2g", "K")
    b = _w1n(df, "1 i9", "M")

    def fake(group, week_mw):
        if week_mw == "10.3":
            return NS(returncode=1, stdout="", stderr="osascript_timeout")
        return NS(returncode=0, stdout="OK:weekRow=44|M=1|\nPRE:M=\n", stderr="")
    monkeypatch.setattr(df, "write_1n_via_daemon", fake)
    monkeypatch.setattr(df, "parse_pre_lines", lambda out: {"M": ""} if "PRE" in out else {})
    res, row_of, pre_of = df.write_1n_batches([a, b], date(2026, 10, 25))
    assert row_of == {id(b): "44"}          # failed 10.3 batch: no row, no credit
    assert id(a) not in pre_of and pre_of[id(b)] == {"M": ""}
    assert res.returncode == 1               # the failure is what gets reported
