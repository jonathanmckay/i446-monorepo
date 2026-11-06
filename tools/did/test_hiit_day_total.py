"""hiit's 0n cell is the day's total across sessions (JM 2026-11-05: 17, 9 and
22 minutes left AE at 17). It stays a SET so a retry or a second log of the
same session can't double it."""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
import janus_done as jd  # noqa: E402


@pytest.fixture(scope="module")
def df():
    spec = importlib.util.spec_from_file_location("df_hiit", HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["df_hiit"] = mod
    spec.loader.exec_module(mod)
    return mod


R = ("1845", "1908")


def test_a_second_session_raises_the_day_total_to_toggl(df):
    assert df.hiit_day_minutes(9, None, None, 26, 17, False) == 26


def test_logging_the_same_session_twice_changes_nothing(df):
    assert df.hiit_day_minutes(23, None, R, 49, 49, True) == 49


def test_a_range_toggl_doesnt_have_yet_is_added(df):
    assert df.hiit_day_minutes(23, None, R, 26, 26, False) == 49


def test_a_bare_close_with_toggl_down_never_wipes_the_cell(df):
    assert df.hiit_day_minutes(1, None, None, 0, 48, False) == 48


def test_a_typed_session_counts_when_toggl_has_nothing(df):
    assert df.hiit_day_minutes(15, 15, None, 0, 0, False) == 15


def test_a_hand_edit_above_toggl_survives(df):
    assert df.hiit_day_minutes(9, None, None, 26, 37, False) == 37


def test_nothing_anywhere_still_marks_it_done(df):
    assert df.hiit_day_minutes(1, None, None, 0, 0, False) == 1


def test_the_row_earns_its_session_not_the_day():
    r = {"name": "hiit", "step": "0n", "value": 48, "session_value": 22}
    assert jd.did_points(r) == 44


def test_a_bare_result_still_prices_its_value():
    assert jd.did_points({"name": "hiit", "step": "0n", "value": 18}) == 36


def test_hiit_stays_a_set_not_an_add(df):
    assert "hiit" not in df.CUMULATIVE_0N
    item = type("I", (), {"name": "hiit", "time_range": R, "time_value": None})
    assert df._adds_0n(type("W", (), {"item": item})) is False


def test_the_total_is_taken_after_the_timer_stops():
    src = (HERE / "did-fast.py").read_text()
    assert src.index("apply_timer_minutes(fast, toggl_stop)") < src.index("apply_hiit_day_total(fast, target_date)")


def test_run_py_hands_hiit_to_did_fast():
    assert '"hiit"}' in (HERE / "run.py").read_text().split("VIA_DID_FAST = ")[1].splitlines()[0]
