"""Regression tests for the eat card's last-meal lookup.

Request (2026-06-25): the /inbound eat card should show the most-recent meal
logged today (from Neon hcbi) plus its time-band glyph, e.g. "last eaten:
mocha + tacos · 午". last_meal_today() reads hcbi via ix and returns
(food, band_glyph) of the latest populated Earthly-Branch band, or None.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("_two_n", HERE / "-2n.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


m = _load()


def test_parses_glyph_and_food(monkeypatch):
    monkeypatch.setattr(m, "_ix_osa_run", lambda *a, **k: "午|||mocha + tacos")
    assert m.last_meal_today() == ("mocha + tacos", "午")


def test_strips_whitespace(monkeypatch):
    monkeypatch.setattr(m, "_ix_osa_run", lambda *a, **k: "未|||  asha small tea \n")
    assert m.last_meal_today() == ("asha small tea", "未")


def test_none_when_ix_unreachable(monkeypatch):
    monkeypatch.setattr(m, "_ix_osa_run", lambda *a, **k: None)
    assert m.last_meal_today() is None


def test_none_when_no_meal_logged(monkeypatch):
    # AppleScript returns "" (no row / all bands empty).
    monkeypatch.setattr(m, "_ix_osa_run", lambda *a, **k: "")
    assert m.last_meal_today() is None


def test_none_on_malformed_output(monkeypatch):
    monkeypatch.setattr(m, "_ix_osa_run", lambda *a, **k: "garbage no delimiter")
    assert m.last_meal_today() is None


def test_bands_ordered_earliest_to_latest():
    # The AppleScript keeps the LAST non-empty band, so ordering must be
    # chronological for "most recent meal" to be correct.
    glyphs = [g for g, _ in m._HCBI_BANDS]
    assert glyphs == ["卯", "辰", "巳", "午", "未", "申", "戌"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
