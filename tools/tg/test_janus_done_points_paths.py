"""JM 2026-10-28: /done on a time entry takes one of three paths —
a Todoist task's [N], a time-based habit/category's minutes, or a prompt
for a total or a per-minute rate. A task already crossed off in dtd adds
nothing; a repeatable habit (xk22) still credits each session."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import janus  # noqa: E402


def _completed(tmp_path, monkeypatch, names):
    ct = tmp_path / "completed-today.json"
    ct.write_text(json.dumps({"date": dt.datetime.now(janus._tz()).date().isoformat(),
                              "names": names, "points": {}}))
    monkeypatch.setattr(janus, "_COMPLETED_TODAY", ct)


def _setup(tmp_path, monkeypatch, names=(), task_pts=None, habits=()):
    _completed(tmp_path, monkeypatch, list(names))
    monkeypatch.setattr(janus, "_resolvable_points",
                        lambda d: (task_pts or {}).get(janus._norm_done_name(d)))
    monkeypatch.setattr(janus, "_habit_tags",
                        lambda tags: [t for t in tags if t in habits])

    class DF:
        ALIASES = {"hit": "hiit"}
        VARIABLE_0N = {"hiit", "xk22"}
        VARIABLE_DOMAIN = {"bball": ("hcbp", "W")}
    monkeypatch.setattr(janus, "_df_tags_mod", lambda: DF)


def test_task_with_points_uses_task_path(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, task_pts={"write memo": 30})
    assert janus._done_points_path("write memo") == "task"


def test_known_variable_and_category_are_time_based(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, habits=("冥想",))
    assert janus._done_points_path("hiit") == "time"
    assert janus._done_points_path("hit") == "time"
    assert janus._done_points_path("bball @hcbp") == "time"
    assert janus._done_points_path("冥想") == "time"


def test_repeatable_habit_done_earlier_still_credits_minutes(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, names=["xk22"])
    assert janus._done_points_path("xk22") == "time"


def test_task_crossed_off_in_dtd_adds_nothing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, names=["call steve"])
    assert janus._done_points_path("call steve") == "credited"


def test_unknown_entry_asks(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert janus._done_points_path("XCORE") == "ask"


def test_points_answer_total_rate_blank_and_garbage():
    assert janus._points_from_answer("40", 18) == 40
    assert janus._points_from_answer(".5/m", 18) == 9
    assert janus._points_from_answer("2/min", 18) == 36
    assert janus._points_from_answer("", 18) == 0
    assert janus._points_from_answer("lots", 18) is None


def test_past_day_row_is_never_credited_by_todays_cross_off(tmp_path, monkeypatch):
    # completed-today.json is today's record: a past-day backfill of an entry
    # whose name was crossed off TODAY must ask (or credit), not skip (v172).
    _setup(tmp_path, monkeypatch, names=["call steve"])
    assert janus._done_points_path("call steve") == "credited"
    assert janus._done_points_path("call steve", past_day=True) == "ask"


def test_row_done_handler_passes_past_day_flag():
    # The ⌥↵ finished-row handler is the only past-day caller; it must pass
    # the view's day, like mobile.py's _done_path(today=None).
    src = (Path(__file__).parent / "janus.py").read_text()
    assert "_done_points_path(desc, past_day=STATE.day_offset != 0)" in src
