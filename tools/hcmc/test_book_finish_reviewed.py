"""book-finish --reviewed --file: publishing a review marks the book finished."""
import datetime
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("book_finish_reviewed", os.path.join(HERE, "book-finish.py"))
bf = importlib.util.module_from_spec(spec)
sys.modules["book_finish_reviewed"] = bf
spec.loader.exec_module(bf)

TODAY = datetime.date(2026, 11, 22)  # a Sunday: the week is 11-22..11-28

REVIEW = '''---
title: "Punchy Line: The Book: A Subtitle"
author: "Ann Author"
date: {date}
type: review
media: book
score: 3
goodreads_review: "https://www.goodreads.com/review/show/1"
{extra}---

**Punchy Line**

Body.
'''


def setup(tmp_path, monkeypatch, date="2026-11-22", extra=""):
    year = tmp_path / "2026"
    year.mkdir()
    f = year / "the-book-a-subtitle.md"
    f.write_text(REVIEW.format(date=date, extra=extra), encoding="utf-8")
    monkeypatch.setattr(bf, "REVIEWS", str(tmp_path))
    bumps = []
    monkeypatch.setattr(bf, "bump_books_read", lambda d: (bumps.append(d), (True, "OK: AM48=2"))[1])
    return f, bumps


def test_no_status_gets_both_lines_and_one_bump(tmp_path, monkeypatch):
    f, bumps = setup(tmp_path, monkeypatch)
    code, msg = bf.finish_reviewed(str(f), TODAY)
    text = f.read_text(encoding="utf-8")
    assert code == 0 and msg.startswith("✓") and "+1" in msg
    assert text.count("status: finished\n") == 1 and text.count("finished: 2026-11-22\n") == 1
    assert text.endswith("---\n\n**Punchy Line**\n\nBody.\n")  # body untouched
    assert 'title: "Punchy Line: The Book: A Subtitle"\n' in text
    assert bumps == [TODAY]


def test_already_finished_is_a_no_op(tmp_path, monkeypatch):
    f, bumps = setup(tmp_path, monkeypatch, extra="status: finished\nfinished: 2026-11-20\n")
    before = f.read_text(encoding="utf-8")
    code, msg = bf.finish_reviewed(str(f), TODAY)
    assert code == 0 and msg.startswith("ALREADY_FINISHED")
    assert f.read_text(encoding="utf-8") == before and bumps == []


def test_reading_status_is_replaced_not_duplicated(tmp_path, monkeypatch):
    f, bumps = setup(tmp_path, monkeypatch, extra="status: reading\n")
    bf.finish_reviewed(str(f), TODAY)
    text = f.read_text(encoding="utf-8")
    assert text.count("status:") == 1 and "status: finished\n" in text and len(bumps) == 1


def test_old_review_keeps_its_date_and_does_not_bump(tmp_path, monkeypatch):
    f, bumps = setup(tmp_path, monkeypatch, date="2019-03-02")
    code, msg = bf.finish_reviewed(str(f), TODAY)
    assert code == 0 and "not this week" in msg
    assert "finished: 2019-03-02\n" in f.read_text(encoding="utf-8") and bumps == []


def test_finished_stub_for_same_book_blocks_the_bump(tmp_path, monkeypatch):
    f, bumps = setup(tmp_path, monkeypatch)
    (tmp_path / "2026" / "the-book.md").write_text(
        '---\ntitle: "The Book"\nauthor: "Ann Author"\nmedia: book\ntype: review\n'
        "status: finished\nfinished: 2026-11-19\n---\n", encoding="utf-8")
    code, msg = bf.finish_reviewed(str(f), TODAY)
    assert code == 0 and msg.startswith("ALREADY_FINISHED") and bumps == []
    assert "finished: 2026-11-19\n" in f.read_text(encoding="utf-8")


def test_other_author_with_same_title_is_not_a_twin(tmp_path, monkeypatch):
    f, bumps = setup(tmp_path, monkeypatch)
    (tmp_path / "2026" / "the-book.md").write_text(
        '---\ntitle: "The Book"\nauthor: "Someone Else"\nmedia: book\ntype: review\n'
        "status: finished\nfinished: 2026-11-19\n---\n", encoding="utf-8")
    bf.finish_reviewed(str(f), TODAY)
    assert len(bumps) == 1


def test_non_review_file_is_refused(tmp_path, monkeypatch):
    f, bumps = setup(tmp_path, monkeypatch)
    f.write_text('---\ntitle: "Index"\ntype: index\nstatus: active\n---\n', encoding="utf-8")
    code, msg = bf.finish_reviewed(str(f), TODAY)
    assert code == 1 and "not a book review" in msg and bumps == []
