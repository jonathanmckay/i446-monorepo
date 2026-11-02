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


def test_entry_block_is_the_block_the_entry_ended_in():
    import datetime as dt
    d = dt.date(2026, 11, 1)
    t = lambda h, m: dt.datetime.combine(d, dt.time(h, m))  # noqa: E731
    assert jd.entry_blocks(t(9, 18), t(12, 41)) == ["巳", "午", "未"]
    assert jd.entry_block(t(9, 18), t(12, 41)) == "未"      # ended in 未, not the 午 majority
    assert jd.entry_block(t(9, 30), t(10, 0)) == "巳"        # last minute 09:59
    assert jd.entry_block(t(8, 30), t(8, 30)) == "巳"        # zero-length: start block
    assert jd.SPLIT_MIN_POINTS == 100


def test_is_rate_answer():
    assert jd.is_rate_answer(".5/m") and jd.is_rate_answer("2/min")
    assert not jd.is_rate_answer("40") and not jd.is_rate_answer("") and not jd.is_rate_answer(None)


def test_second_same_name_session_is_not_crossed_off_by_its_own_points_log():
    """JM 2026-11-01: three interview prep entries, only the first earned 分.
    The first /done ("ask" path) records the name with no Todoist id; that
    must not read as "crossed off", or every later session gets "credited"."""
    norm = lambda s: " ".join(s.split()).lower()
    logged = {"names": ["interview prep"], "points": {"interview prep": 406}, "ids": {}}
    assert not jd.crossed_off(logged, "Interview prep", norm)
    dtd = {"names": ["call steve"], "ids": {"call steve": "6abc"}}
    assert jd.crossed_off(dtd, "call steve", norm)
    assert not jd.crossed_off(dtd, "", norm)
