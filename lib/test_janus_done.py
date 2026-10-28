"""lib/janus_done.py — the /done points rule shared by desktop janus and
mobile janus (web + iOS). JM 2026-10-28."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import janus_done as jd  # noqa: E402


class DF:
    ALIASES = {"hit": "hiit"}
    VARIABLE_0N = {"hiit", "xk22"}
    VARIABLE_DOMAIN = {"bball": ("hcbp", "W")}


def test_classify_order_task_then_time_then_credited_then_ask():
    assert jd.classify(task_points=30, time_based=True, crossed_off=True) == "task"
    assert jd.classify(task_points=None, time_based=True, crossed_off=True) == "time"
    assert jd.classify(task_points=None, time_based=False, crossed_off=True) == "credited"
    assert jd.classify(task_points=None, time_based=False, crossed_off=False) == "ask"


def test_is_time_based_habits_aliases_and_categories():
    habit = {"冥想"}.__contains__
    assert jd.is_time_based("冥想", is_habit=habit, df=DF)
    assert jd.is_time_based("hit", is_habit=habit, df=DF)
    assert jd.is_time_based("bball", is_habit=habit, df=DF)
    assert not jd.is_time_based("xcore", is_habit=habit, df=DF)
    assert not jd.is_time_based("", is_habit=habit, df=DF)


def test_points_from_answer():
    assert jd.points_from_answer("40", 18) == 40
    assert jd.points_from_answer(".5/m", 18) == 9
    assert jd.points_from_answer("2/min", 18) == 36
    assert jd.points_from_answer("", 18) == 0
    assert jd.points_from_answer(None, 18) == 0
    assert jd.points_from_answer("lots", 18) is None
