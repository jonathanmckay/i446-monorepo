"""v182: 4g last-good cache + OneDrive dataless census (dream-intake)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "dream_intake_4g", Path(__file__).parent / "dream-intake.py")
intake = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(intake)

openpyxl = pytest.importorskip("openpyxl")


def _workbook(path: Path):
    import datetime
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = datetime.date.today().strftime("%y") + " 4g"
    ws.append(["tl;dr", "compound the boring stuff"])
    ws.append(["m5x2", "Fund V close", 5000, 0.4, 2000])
    ws.append(["i9", "Ship ExP v2", 3000, 0.9, 2700])
    wb.save(path)


def test_success_writes_cache_then_dataless_serves_it(tmp_path, monkeypatch):
    xlsx, cache = tmp_path / "4g s.xlsx", tmp_path / "c" / "4g.json"
    _workbook(xlsx)
    live = intake._annual_goals_4g(path=xlsx, cache=cache)
    assert live["goal_count"] == 2 and "source" not in live
    assert json.loads(cache.read_text())["result"]["goal_count"] == 2

    monkeypatch.setattr(intake, "_is_dataless", lambda p: True)
    r = intake._annual_goals_4g(path=xlsx, cache=cache)
    assert r["source"] == "last-good-cache" and r["dataless"] is True
    assert [g["domain"] for g in r["goals"]] == ["m5x2", "i9"]
    assert r["tldr"] == "compound the boring stuff"
    assert "Always Keep" in r["error"] and r["cache_age_days"] == 0.0


def test_permission_error_uses_cache(tmp_path, monkeypatch):
    xlsx, cache = tmp_path / "4g s.xlsx", tmp_path / "4g.json"
    _workbook(xlsx)
    intake._annual_goals_4g(path=xlsx, cache=cache)

    def denied(path):
        raise PermissionError("[Errno 1] Operation not permitted")
    monkeypatch.setattr(intake, "_parse_4g_workbook", denied)
    r = intake._annual_goals_4g(path=xlsx, cache=cache)
    assert r["source"] == "last-good-cache" and r["goal_count"] == 2
    assert "dataless" not in r


def test_no_cache_falls_back_to_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(intake.Path, "home", classmethod(lambda cls: tmp_path))
    r = intake._annual_goals_4g(path=tmp_path / "missing.xlsx", cache=tmp_path / "none.json")
    assert r["source"] == "vault-fallback"


def test_corrupt_cache_falls_back_to_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(intake.Path, "home", classmethod(lambda cls: tmp_path))
    bad = tmp_path / "4g.json"
    bad.write_text("{not json")
    r = intake._annual_goals_4g(path=tmp_path / "missing.xlsx", cache=bad)
    assert r["source"] == "vault-fallback"


def test_dataless_census_and_diagnostic(tmp_path, monkeypatch):
    for n in ("4g s.xlsx", "scorecard.xlsx", "Neon.xlsx", ".canary.txt"):
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "m5x2").mkdir()
    monkeypatch.setattr(intake, "_is_dataless", lambda p: p.name != "Neon.xlsx")
    d = intake._onedrive_dataless(root=tmp_path)
    assert d["dataless"] == ["4g s.xlsx", "scorecard.xlsx"] and d["count"] == 2
    line = intake.dataless_diagnostic({"onedrive_dataless": {"status": "ok", "data": d}})
    assert line.startswith("OneDrive: 2 vault-excel workbooks are cloud-only (4g s.xlsx, scorecard.xlsx)")
    assert "Always Keep" in line


def test_dataless_diagnostic_none_when_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "_is_dataless", lambda p: False)
    (tmp_path / "a.xlsx").write_bytes(b"x")
    d = intake._onedrive_dataless(root=tmp_path)
    assert d == {"count": 0, "dataless": []}
    assert intake.dataless_diagnostic({"onedrive_dataless": {"data": d}}) is None
    assert intake.dataless_diagnostic({}) is None


def test_real_dataless_flag_on_plain_file(tmp_path):
    f = tmp_path / "plain.xlsx"
    f.write_bytes(b"x")
    assert intake._is_dataless(f) is False
