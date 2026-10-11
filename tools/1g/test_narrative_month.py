"""Tests for narrative_month.py: the monthly 2g+1g doc must exist before its
first week starts, without JM creating it by hand (2026-10-11: /1s read the
dead 1g sheet because nothing pointed it at the October doc)."""
import importlib.util
from datetime import date
from pathlib import Path

_P = Path(__file__).parent / "narrative_month.py"
_S = importlib.util.spec_from_file_location("nm", _P)
nm = importlib.util.module_from_spec(_S)
_S.loader.exec_module(nm)

INDEX = """---
title: "52 — Monthly/Quarterly Scorecards"
updated: 2026-10-05
---

## 2026 monthly narratives (2g + weekly 1g)
- [[2026.10 2g+1g-narrative|2026.10]]
- [[2026.09 2g+1g-narrative|2026.09]]
"""


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(nm.r1g, "NARRATIVE_DIR", tmp_path)
    monkeypatch.setattr(nm, "INDEX", tmp_path / "5e2.md")
    (tmp_path / "5e2.md").write_text(INDEX)
    (tmp_path / "2026.10 2g+1g-narrative.md").write_text("october")


def test_week_headings_match_what_1g_review_parses(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    nm.ensure(2026, 11, date(2026, 10, 25))
    text = (tmp_path / "2026.11 2g+1g-narrative.md").read_text()
    lines = text.splitlines()
    for sun in nm.month_sundays(2026, 11):
        sat = sun + nm.timedelta(days=6)
        assert any(nm.r1g._heading_re(sun, sat).match(l) for l in lines), sun
    # Nov 29's week runs into December but stays in November's doc
    assert "- **Nov 29 – Dec 5** —" in text
    assert "Previous month: [[2026.10 2g+1g-narrative]]." in text


def test_month_starting_midweek_begins_at_first_sunday():
    assert nm.month_sundays(2026, 12)[0] == date(2026, 12, 6)
    assert nm.month_sundays(2026, 11) == [date(2026, 11, d) for d in (1, 8, 15, 22, 29)]


def test_index_gets_new_month_on_top_and_existing_doc_untouched(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert nm.ensure(2026, 10, date(2026, 10, 25)) is None
    assert (tmp_path / "2026.10 2g+1g-narrative.md").read_text() == "october"
    nm.ensure(2026, 11, date(2026, 10, 25))
    idx = (tmp_path / "5e2.md").read_text()
    assert "(2g + weekly 1g)\n- [[2026.11 2g+1g-narrative|2026.11]]\n- [[2026.10" in idx
    assert "updated: 2026-10-25" in idx
    nm.ensure(2026, 11, date(2026, 10, 26))
    assert idx == (tmp_path / "5e2.md").read_text()


def test_new_year_gets_its_own_heading_above_the_old_one(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    nm.ensure(2027, 1, date(2026, 12, 27))
    idx = (tmp_path / "5e2.md").read_text()
    assert idx.index("## 2027 monthly narratives") < idx.index("## 2026 monthly narratives")
    assert "## 2027 monthly narratives (2g + weekly 1g)\n- [[2027.01 2g+1g-narrative|2027.01]]" in idx
