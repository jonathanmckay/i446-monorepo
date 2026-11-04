"""Tests for dream-intake weekly_goals_1g + annual_goals_4g dataless guard (v174)."""
from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "dream_intake", Path(__file__).parent / "dream-intake.py")
intake = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(intake)

OCT = """---
title: "2g+1g Narrative"
---

## 2026-10 (October)

### Weekly arc

- **Oct 18 – Oct 24** —
\t- hcb
\t\t- 900分
\t\t\t- ❌ none <!--1g-->
- **Oct 25 – Oct 31** — LA week
\t- i9
\t\t- 2000分
\t- m5x2
\t\t- 700分

### Themes

-
"""
NOV = """## 2026-11

### Weekly arc

- **Nov 1 – Nov 7** —
- **Nov 29 – Dec 5** —
"""


def _vault(tmp_path):
    d = tmp_path / "g245" / "5e2"
    d.mkdir(parents=True)
    (d / "2026.10 2g+1g-narrative.md").write_text(OCT, encoding="utf-8")
    (d / "2026.11 2g+1g-narrative.md").write_text(NOV, encoding="utf-8")
    return tmp_path


def test_parse_weeks_strips_markers_and_stops_at_heading():
    weeks = intake.parse_1g_weeks(OCT, 2026)
    assert [w["start"].isoformat() for w in weeks] == ["2026-10-18", "2026-10-25"]
    assert weeks[0]["lines"][-1].strip() == "- ❌ none"
    assert weeks[1]["note"] == "LA week"
    assert not any("Themes" in l for l in weeks[1]["lines"])


def test_current_week_saturday(tmp_path):
    r = intake.current_1g_week(_vault(tmp_path), datetime.date(2026, 10, 31))
    assert r["week"] == "2026-10-25..2026-10-31"
    assert not r["empty"] and not r["stale"]
    assert any("2000分" in l for l in r["goals"])


def test_next_week_in_next_month_file_empty(tmp_path):
    r = intake.current_1g_week(_vault(tmp_path), datetime.date(2026, 11, 1))
    assert r["week"] == "2026-11-01..2026-11-07"
    assert r["empty"] is True and r["stale"] is False


def test_cross_month_week(tmp_path):
    r = intake.current_1g_week(_vault(tmp_path), datetime.date(2026, 12, 2))
    assert r["week"] == "2026-11-29..2026-12-05"


def test_dataless_guard(tmp_path, monkeypatch):
    f = tmp_path / "x.xlsx"
    f.write_bytes(b"")
    assert intake._is_dataless(f) is False
    monkeypatch.setattr(intake, "_is_dataless", lambda p: True)
    monkeypatch.setattr(intake.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "OneDrive" / "vault-excel").mkdir(parents=True)
    (tmp_path / "OneDrive" / "vault-excel" / "4g s.xlsx").write_bytes(b"")
    r = intake._annual_goals_4g()
    assert r["dataless"] is True and "Always Keep" in r["error"]
