"""/1hcmc: 3 bullets on each book finished that week and not yet reviewed. They
go to the book's review file, which jmreads reads, never to Neon (JM 2026-11-05)."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("hcmc_survey", HERE / "hcmc-survey.py")
hs = importlib.util.module_from_spec(spec)
sys.modules["hcmc_survey"] = hs
spec.loader.exec_module(hs)

START, END = dt.date(2026, 10, 28), dt.date(2026, 11, 3)


def _book(root, slug, finished="2026-11-01", body="Subjects: Space Opera\n", **fm):
    d = root / "2026"
    d.mkdir(exist_ok=True)
    extra = "".join(f"{k}: {v}\n" for k, v in fm.items())
    (d / f"{slug}.md").write_text(
        f'---\ntitle: "{slug}"\nauthor: "A"\ndate: 2026-10-01\ntype: review\nmedia: book\n'
        f"status: finished\nfinished: {finished}\n{extra}---\n\n{body}", encoding="utf-8")


@pytest.fixture()
def jr(tmp_path, monkeypatch):
    mod = hs._jmreads()
    monkeypatch.setattr(mod, "REVIEWS_DIR", tmp_path)
    monkeypatch.setattr(mod, "BLOG_REVIEWS_DIR", tmp_path / "blog")
    monkeypatch.setattr(mod, "invalidate", lambda: None)
    return mod


def test_finds_a_stub_finished_in_the_week(jr, tmp_path):
    _book(tmp_path, "stub")
    assert [b["id"] for b in hs.unreviewed_books(START, END, jr)] == ["stub"]


def test_skips_books_outside_the_week_reviewed_or_scored(jr, tmp_path):
    _book(tmp_path, "last-week", finished="2026-10-27")
    _book(tmp_path, "reviewed", body="A long enough review of this book to count as a real one, surely.\n")
    _book(tmp_path, "scored", score=4)
    assert hs.unreviewed_books(START, END, jr) == []


def test_notes_land_in_the_review_file_and_the_book_stops_asking(jr, tmp_path):
    _book(tmp_path, "stub")
    ok, msg = hs.save_book_notes("stub", "first\nsecond\nthird", jr)
    assert ok, msg
    text = (tmp_path / "2026" / "stub.md").read_text(encoding="utf-8")
    assert "• first" in text and "• third" in text
    assert hs.unreviewed_books(START, END, jr) == []


def test_empty_notes_write_nothing(jr, tmp_path):
    _book(tmp_path, "stub")
    assert hs.save_book_notes("stub", "  \n ", jr)[0] is False
    assert [b["id"] for b in hs.unreviewed_books(START, END, jr)] == ["stub"]


def test_prompt_takes_three_bullets_and_a_blank_skips(jr, tmp_path, monkeypatch):
    _book(tmp_path, "one")
    _book(tmp_path, "two")
    answers = iter(["a", "b", "c", ""])
    monkeypatch.setattr(hs, "read_line", lambda prompt: next(answers))
    hs.ask_book_notes(START, END, jr)
    assert "• c" in (tmp_path / "2026" / "one.md").read_text(encoding="utf-8")
    assert "draft" not in (tmp_path / "2026" / "two.md").read_text(encoding="utf-8")


def test_notes_never_touch_neon(jr, tmp_path, monkeypatch):
    _book(tmp_path, "stub")
    monkeypatch.setattr(hs, "osa", lambda s: pytest.fail("book notes must not reach Excel"))
    assert hs.save_book_notes("stub", "x", jr)[0]
