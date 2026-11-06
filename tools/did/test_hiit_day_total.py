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
    assert src.index("apply_timer_minutes(fast, toggl_stop)") < src.index("apply_hiit_day_total(fast, target_date")


def test_run_py_hands_hiit_to_did_fast():
    assert '"hiit"}' in (HERE / "run.py").read_text().split("VIA_DID_FAST = ")[1].splitlines()[0]


def test_main_sets_target_date_before_the_hiit_step():
    # 2026-11-05: the call sat seven lines above the assignment, so every
    # did-fast run died with UnboundLocalError and nothing could be completed.
    src = (Path(__file__).parent / "did-fast.py").read_text()
    main = src[src.index("\ndef main():"):]
    assert main.index("    target_date = items[0].target_date") < main.index("apply_hiit_day_total(fast, target_date")



def test_a_stopped_timer_is_the_sessions_minutes(df, monkeypatch):
    """2026-11-06 (Dream v180 code-review): a bare /done on a running 9m hiit
    after a 17m morning session. Routing sums the day (26) and
    apply_timer_minutes leaves it, so without the stopped timer the entry's
    record priced the day total: [52] on a 9m row instead of [18]."""
    from types import SimpleNamespace
    item = SimpleNamespace(name="hiit", time_value=None, time_range=None)
    r = df.RouteResult(item=item, step="0n", col_num=31, write_value=26)
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 26)
    monkeypatch.setattr(df.neon_excel, "read", lambda *a, **k: {"value": 17})
    stop = {"description": "hiit", "stopped": True, "minutes": 9, "id": "1"}
    df.apply_hiit_day_total([r], "11/5", stop)
    assert r.write_value == 26          # the cell still gets the day total
    assert r.session_minutes == 9
    assert jd.did_points({"name": "hiit", "step": "0n", "value": r.write_value,
                          "session_value": r.session_minutes}) == 18


def test_another_timer_stopping_is_not_the_session(df, monkeypatch):
    from types import SimpleNamespace
    item = SimpleNamespace(name="hiit", time_value=None, time_range=None)
    r = df.RouteResult(item=item, step="0n", col_num=31, write_value=26)
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 26)
    monkeypatch.setattr(df.neon_excel, "read", lambda *a, **k: {"value": 26})
    df.apply_hiit_day_total([r], "11/5", {"description": "o314", "stopped": True, "minutes": 40})
    assert r.session_minutes is None
