"""
/1s897 AB cell: JM's notes and the bot's 😈 commentary share one cell.

Feature (2026-11-25): JM stopped opening Excel, so the bot's analysis lines
carry a 😈 prefix and the skill asks him for his own notes. On 2026-11-25 the
skill rewrote AB three times in one morning, the second write putting his two
notes under five bot bullets and the third dropping the bullets: each voice
must be replaceable without touching the other.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("ab_notes", Path(__file__).with_name("ab_notes.py"))
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)

JM = "• Move to 1 s897 thing every day. More planning, less doing\n• One hiring thing every day"
BOT = "😈 Thin week: 157m, 135₹ of 240₹\n😈 Zero friend in-person"


def test_bot_lines_get_demon_prefix_and_bullets_are_stripped():
    out = ab.merge("", bot="• Thin week\n- Zero friend in-person\n\n")
    assert out == "😈 Thin week\n😈 Zero friend in-person"


def test_bot_write_keeps_jm_lines_above():
    out = ab.merge(JM, bot="Thin week")
    assert out == JM + "\n😈 Thin week"


def test_bot_rerun_replaces_only_demon_lines():
    out = ab.merge(JM + "\n" + BOT, bot="New take")
    assert out == JM + "\n😈 New take"


def test_jm_write_keeps_bot_lines_below():
    out = ab.merge(BOT, jm="one s897 thing a day\n2. one hiring thing a day")
    assert out == "• one s897 thing a day\n• one hiring thing a day\n" + BOT


def test_jm_rewrite_replaces_only_his_lines():
    out = ab.merge(JM + "\n" + BOT, jm="just this")
    assert out == "• just this\n" + BOT


def test_jm_lines_sort_above_bot_lines_whatever_the_stored_order():
    out = ab.merge(BOT + "\n" + JM, bot="again")
    assert out == JM + "\n😈 again"


def test_demon_inside_a_jm_line_does_not_make_it_a_bot_line():
    cell = "• the 😈 lines were useful"
    assert ab.split(cell) == ([cell], [])
    assert ab.merge(cell, bot="x") == cell + "\n😈 x"


def test_empty_answer_clears_that_voice_only():
    assert ab.merge(JM + "\n" + BOT, jm="  \n") == BOT
