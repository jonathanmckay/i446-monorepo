"""Tests for book_dir.py: books file into the kid's newest grade folder.

Regression (2026-10-24): SKILL.md sent new books to a `readers/` folder that
never existed, while real books lived in each kid's grade folder.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("book_dir", Path(__file__).with_name("book_dir.py"))
bd = importlib.util.module_from_spec(_spec)
sys.modules["book_dir"] = bd
_spec.loader.exec_module(bd)


@pytest.fixture
def base(tmp_path):
    for d in ["theo-pk3-2025-08", "theo-pk2-2024-08", "ren-pk2-2025-09",
              "theo-k-2026-08-notes", "readers"]:
        (tmp_path / d).mkdir()
    (tmp_path / "theo-k-2027-01.md").write_text("a note, not a folder")
    return tmp_path


def test_newest_grade_folder_wins(base):
    assert bd.current_grade_dir("theo", base).name == "theo-pk3-2025-08"
    (base / "theo-k-2026-08").mkdir()
    assert bd.current_grade_dir("Theo", base).name == "theo-k-2026-08"


def test_month_breaks_year_tie(base):
    (base / "ren-pk3-2025-11").mkdir()
    assert bd.current_grade_dir("ren", base).name == "ren-pk3-2025-11"


def test_no_grade_folder_raises(base):
    with pytest.raises(ValueError):
        bd.current_grade_dir("aurora", base)


def test_book_paths_layout_and_tracker(base):
    p = bd.book_paths("ren", "bobo-and-the-tan-bag", base)
    book = base / "ren-pk2-2025-09" / "bobo-and-the-tan-bag"
    assert p["book_dir"] == str(book)
    assert p["md"] == str(book / "bobo-and-the-tan-bag.md")
    assert p["pdf"] == str(book / "bobo-and-the-tan-bag.pdf")
    assert p["tracker"] == str(base / "xk23-english-reader-tracker.md")
    assert bd.book_paths("theo", "bobo-moon", base)["tracker"] == str(base / "xk23-chinese-reader-tracker.md")
    assert "readers" not in p["book_dir"]


def test_slug_must_be_kebab(base):
    for bad in ["波波去月亮", "Bobo Moon", "bobo_moon", ""]:
        with pytest.raises(ValueError):
            bd.book_paths("theo", bad, base)


def test_live_vault_trackers_exist():
    if not bd.CURRICULUM.is_dir():
        pytest.skip("no vault on this machine")
    for kid, tracker in bd.TRACKERS.items():
        assert (bd.CURRICULUM / tracker).is_file()
        bd.current_grade_dir(kid)   # every kid with a tracker has a grade folder
