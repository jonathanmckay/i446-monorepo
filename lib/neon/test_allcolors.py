"""lib/neon/allcolors.py — all-colors / handicap rule."""
from __future__ import annotations

import datetime as dt

from neon import allcolors as ac

D = dt.date(2026, 11, 2)


def t(h, m=0):
    return dt.datetime.combine(D, dt.time(h, m))


def done(name, at=None):
    return ac.Cell(name, ac.DONE, at)


CORE = [done("0t", t(8)), done("0l", t(12, 52)), done("0g", t(9))]


def test_classify_value():
    assert ac.classify_value("") == ac.BLANK
    assert ac.classify_value("missing value") == ac.BLANK
    assert ac.classify_value("0") == ac.NA
    assert ac.classify_value("0.0") == ac.NA
    assert ac.classify_value("8.0") == ac.DONE
    assert ac.classify_value("x") == ac.DONE


def test_core_required():
    core = CORE[:2] + [ac.Cell("0g", ac.BLANK)]
    assert ac.evaluate(D, core, [], handicap=99) is None


def test_time_is_when_threshold_crossed():
    habits = [done("a", t(7)), done("b", t(19, 22)), ac.Cell("c", ac.BLANK),
              ac.Cell("d", ac.NA)]
    # 3 applicable, handicap 1 → need 2 done → crossed at b (19:22)
    assert ac.evaluate(D, CORE, habits, handicap=1) == t(19, 22)
    # handicap 2 → need 1 → crossed when core finished (0l 12:52)
    assert ac.evaluate(D, CORE, habits, handicap=2) == t(12, 52)
    assert ac.evaluate(D, CORE, habits, handicap=0) is None


def test_unknown_time_uses_fallback():
    habits = [done("a")]
    assert ac.evaluate(D, CORE, habits, 0) == t(23, 59)
    assert ac.evaluate(D, CORE, habits, 0, unknown=t(15)) == t(15)


def test_onen_cell():
    assert ac.onen_cell([]).status == ac.NA
    assert ac.onen_cell([ac.Cell("x", ac.NA)]).status == ac.NA
    assert ac.onen_cell([done("x", t(1)), ac.Cell("y", ac.BLANK)]).status == ac.BLANK
    c = ac.onen_cell([done("x", t(1)), done("y", t(5)), ac.Cell("z", ac.NA)])
    assert (c.status, c.at) == (ac.DONE, t(5))
    assert ac.onen_cell([done("x", t(1)), done("y")]).at is None


def test_next_handicap():
    assert ac.next_handicap("3.0", "") == 4
    assert ac.next_handicap("3.0", "1253.0") == 2
    assert ac.next_handicap("0", "1515") == 0
    assert ac.next_handicap("", "") == 1


def test_habit_headers():
    assert not ac.is_habit_header("词汇")
    assert not ac.is_habit_header("⎣∀clr")
    assert not ac.is_habit_header("N color")
    assert ac.is_habit_header("NVC\xa0+ e")


# ── N color (0n!AF): JM 2026-11-06, the first moment 0t and 0l were both done ──

def test_n_color_is_whichever_of_0t_0l_finished_last():
    assert ac.n_color_time(done("0t", t(6, 57)), done("0l", t(6, 12))) == t(6, 57)
    assert ac.n_color_time(done("0t", t(9, 8)), done("0l", t(9, 45))) == t(9, 45)


def test_n_color_needs_both():
    blank = ac.Cell("0t", ac.BLANK)
    na = ac.Cell("0t", ac.NA)
    assert ac.n_color_time(blank, done("0l", t(8))) is None
    assert ac.n_color_time(na, done("0l", t(8))) is None
    assert ac.n_color_time(done("0l", t(8)), ac.Cell("0l", ac.BLANK)) is None


def test_n_color_unknown_time_uses_the_fallback():
    assert ac.n_color_time(done("0t"), done("0l", t(8)), unknown=t(10)) == t(10)
    assert ac.n_color_time(done("0t"), done("0l", t(8))) == t(23, 59)
