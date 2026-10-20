"""--past-ok (2026-09-28): Janus's past-day view writes a 0₦ habit onto the
viewed day's 0n row instead of Step 0.1's posthoc detour, with no today-side
effects. Default behaviour (no flag) is unchanged."""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("df_pastok", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["df_pastok"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def df():
    return _load()


HEADERS = {"0n": {"0l": 7, "notes": 9}, "1n": {}}
TQ = {"0neon": [{"id": "t1", "content": "0l", "labels": ["0neon"], "due": "2026-09-28"}],
      "夜neon": [], "1neon": [], "today": []}


def _past_md(df):
    t = df._daytime.today()
    y = t - df.timedelta(days=3)
    return f"{y.month}/{y.day}"


def test_default_past_date_still_takes_posthoc_detour(df):
    items = df.parse_input(f"0l {_past_md(df)}")
    r = df.route_items(items, HEADERS, TQ)[0]
    assert r.step == "variable" and r.error == "0neon" and r.col_num is None


def test_past_ok_writes_the_past_row_with_value_1_and_no_card_close(df):
    items = df.parse_input(f"0l {_past_md(df)}")
    r = df.route_items(items, HEADERS, TQ, past_ok=True)[0]
    assert r.step == "0n" and r.col_num == 7
    assert r.write_value == 1, "never today's Toggl minutes for a past day"
    assert r.todoist_task is None, "today's recurring 0neon card must stay open"
    assert r.item.target_date == _past_md(df)


def test_past_ok_keeps_an_explicit_value(df):
    items = df.parse_input(f"notes 25 {_past_md(df)}")
    r = df.route_items(items, HEADERS, TQ, past_ok=True)[0]
    assert r.step == "0n" and r.write_value == 25


def test_past_ok_on_today_is_a_no_op(df):
    """The flag only changes PAST-date routing: today still matches and
    closes the card as before."""
    items = df.parse_input("0l")
    r_default = df.route_items(items, HEADERS, TQ)[0]
    r_flag = df.route_items(items, HEADERS, TQ, past_ok=True)[0]
    assert r_default.step == r_flag.step == "0n"
    assert (r_flag.todoist_task or {}).get("id") == (r_default.todoist_task or {}).get("id") == "t1"


def test_past_target_helper(df):
    t = df._daytime.today()
    today_items = df.parse_input("0l")
    past_items = df.parse_input(f"0l {_past_md(df)}")
    assert df._past_target(today_items, True) is False
    assert df._past_target(past_items, False) is False
    assert df._past_target(past_items, True) is True
    assert df._past_target([], True) is False
    assert f"{t.month}/{t.day}" == today_items[0].target_date
