"""JM 2026-09-17: "Can we make that a batch write or something a little more
atomic?" — a Ctrl-C two seconds into the old 3+N-append /ate left the name
and kcal written and the protein/groups missing. Now one batch request."""
import importlib.util
import signal
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("ate_t", HERE / "ate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_groups_accepts_the_loose_skill_syntax():
    m = _load()
    assert m.parse_groups("{wtr, flx x 2, vegetables x 2, grain, beans, spice}") == \
        [("wtr", 1), ("fx", 2), ("vg", 2), ("g", 1), ("bn", 1), ("sp", 1)]
    assert m.parse_groups("(berries 3, grains 1)") == [("br", 3), ("g", 1)]
    assert m.parse_groups("br:3, flax x2") == [("br", 3), ("fx", 2)]
    assert m.parse_groups(None) == [] and m.parse_groups("") == []
    with pytest.raises(ValueError):
        m.parse_groups("plutonium 2")


def test_eval_num_handles_arithmetic_and_rejects_code():
    m = _load()
    assert m.eval_num("60+40+250+200") == 550
    assert m.eval_num("4+1+9+10") == 24
    assert m.eval_num(None) is None and m.eval_num(" ") is None
    with pytest.raises(ValueError):
        m.eval_num("__import__('os')")


def _fake_neon(monkeypatch, m, calls):
    band = {"branch": "辰", "hours": "08:00-09:59", "cols": ["AN", "AO", "AP"]}
    cols = types.SimpleNamespace(
        hcbi_band=lambda h: band, hcbi_band_by_branch=lambda b: band,
        daily_dozen_col=lambda ab: {"wtr": "O", "fx": "K", "vg": "J", "g": "L", "bn": "E", "sp": "N"}[ab],
        col=lambda sheet, h: {"cal": "U", "0s": "T"}[h])
    excel = types.SimpleNamespace(
        batch_append=lambda sheet, items, **kw: calls.append(("batch", sheet, items, kw)) or {"ok": True, "row": 258},
        append=lambda *a, **k: calls.append(("append", a, k)),
        read=lambda sheet, col, **kw: {"value": {"U": "800", "T": ""}[col]},
        write=lambda sheet, col, **kw: calls.append(("write", col, kw)))
    monkeypatch.setitem(sys.modules, "neon", types.SimpleNamespace(cols=cols, excel=excel))
    monkeypatch.setitem(sys.modules, "neon.cols", cols)
    monkeypatch.setitem(sys.modules, "neon.excel", excel)
    return band


def test_main_sends_exactly_one_batch_and_never_single_appends(monkeypatch, capsys):
    m = _load()
    calls = []
    _fake_neon(monkeypatch, m, calls)
    rc = m.main(["--name", "flax x2 + huevos rancheros + mocha", "--kcal", "60+40+250+200",
                 "--protein", "4+1+9+10", "--groups", "{wtr, flx x 2, vegetables x 2, grain, beans, spice}",
                 "--date", "9/17"])
    assert rc == 0
    kinds = [c[0] for c in calls]
    assert kinds.count("batch") == 1 and "append" not in kinds
    _, sheet, items, kw = calls[0]
    assert sheet == "hcbi" and kw["date"] == "9/17"
    assert items[:3] == [{"col": "AN", "value": ", flax x2 + huevos rancheros + mocha"},
                         {"col": "AO", "value": "+550"}, {"col": "AP", "value": "+24"}]
    assert [(i["col"], i["value"]) for i in items[3:]] == \
        [("O", "+1"), ("K", "+2"), ("J", "+2"), ("L", "+1"), ("E", "+1"), ("N", "+1")]
    # tier: 800 kcal → 10, T was blank → written
    assert ("write", "T", {"date": "9/17", "value": "10", "src": "ate-tier"}) in calls
    out = capsys.readouterr().out
    assert "row 258" in out and "+10 tracking pts" in out


def test_skip_food_writes_only_groups(monkeypatch):
    m = _load()
    calls = []
    _fake_neon(monkeypatch, m, calls)
    assert m.main(["--skip-food", "--groups", "flax 2", "--date", "9/17"]) == 0
    items = calls[0][2]
    assert items == [{"col": "K", "value": "+2"}]


def test_sigint_is_ignored_during_the_batch_and_restored_after(monkeypatch):
    m = _load()
    seen = {}

    def batch(sheet, items, **kw):
        seen["handler"] = signal.getsignal(signal.SIGINT)
        return {"ok": True, "row": 1}
    calls = []
    _fake_neon(monkeypatch, m, calls)
    sys.modules["neon"].excel.batch_append = batch
    sys.modules["neon.excel"].batch_append = batch
    before = signal.getsignal(signal.SIGINT)
    assert m.main(["--name", "x", "--kcal", "1", "--protein", "0", "--date", "9/17"]) == 0
    assert seen["handler"] is signal.SIG_IGN
    assert signal.getsignal(signal.SIGINT) is before


def test_dry_run_writes_nothing(monkeypatch, capsys):
    m = _load()
    calls = []
    _fake_neon(monkeypatch, m, calls)
    assert m.main(["--name", "x", "--kcal", "1", "--dry-run", "--date", "9/17"]) == 0
    assert calls == []
    assert '"AN"' in capsys.readouterr().out
