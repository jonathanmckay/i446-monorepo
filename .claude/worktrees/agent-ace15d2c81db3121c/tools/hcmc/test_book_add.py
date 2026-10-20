"""Tests for book-add.py (no network)."""
import datetime
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("book_add", os.path.join(HERE, "book-add.py"))
ba = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ba)


def test_slugify_basics():
    assert ba.slugify("The Three-Body Problem") == "the-three-body-problem"
    assert ba.slugify("Assassin's Quest (Farseer #3)") == "assassins-quest-farseer-3"
    assert ba.slugify("三体") == "三体"


def test_rank_keeps_engine_order_but_demotes_authorless():
    cands = [{"title": "A", "author": ""}, {"title": "B", "author": "X"},
             {"title": "C", "author": "Y"}]
    assert [c["title"] for c in ba.rank(cands, "q")] == ["B", "C", "A"]


def test_make_note_frontmatter_matches_reviews_convention():
    c = {"title": "三体", "author": "刘慈欣", "year": 2008, "pages": 417,
         "isbn": "123", "subjects": ["Science fiction"]}
    note = ba.make_note(c, datetime.date(2026, 7, 29))
    assert 'title: "三体"' in note and 'author: "刘慈欣"' in note
    assert "media: book" in note and "status: reading" in note and "draft: true" in note
    assert note.startswith("---") and "tags: [hcmc, review]" in note


def test_make_note_omits_missing_fields():
    note = ba.make_note({"title": "X", "author": "", "year": None,
                         "pages": None, "isbn": None, "subjects": []},
                        datetime.date(2026, 7, 29))
    assert "author:" not in note and "published:" not in note and "isbn:" not in note
