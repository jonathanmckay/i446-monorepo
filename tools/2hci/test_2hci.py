"""2hci window + classification logic (no Excel, no network)."""
import importlib.util
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("hci2", HERE / "2hci.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hci2"] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load()


def test_window_is_26th_to_25th():
    assert m.window_for(2026, 10) == (date(2026, 9, 26), date(2026, 10, 25))


def test_january_window_crosses_year():
    assert m.window_for(2027, 1) == (date(2026, 12, 26), date(2027, 1, 25))


def test_default_on_the_26th_is_the_window_just_ended():
    assert m.default_window(date(2026, 10, 26)) == (date(2026, 9, 26), date(2026, 10, 25))


def test_default_before_the_26th_is_previous_window():
    assert m.default_window(date(2026, 10, 25)) == (date(2026, 8, 26), date(2026, 9, 25))
    assert m.default_window(date(2027, 1, 3)) == (date(2026, 11, 26), date(2026, 12, 25))


def test_parse_month_forms():
    today = date(2026, 10, 26)
    assert m.parse_month("10", today) == (2026, 10)
    assert m.parse_month("oct", today) == (2026, 10)
    assert m.parse_month("2025-12", today) == (2025, 12)


def test_score_full_na_miss():
    y = date.today().year
    vals = {"3/1": "1", "3/2": "15", "3/3": "0", "3/4": ""}
    s = m.score_habit(vals, date(y, 3, 1), date(y, 3, 5))
    assert (s["full"], s["na"], s["miss"], s["missing"]) == (2, 1, 1, 1)
    assert s["minutes"] == 15


def test_other_hci_excludes_habit_cards_and_defers():
    ok = {"content": "more hair (5) [10]", "labels": ["hci"]}
    assert m.is_other_hci(ok)
    for c in ["1st hci (15) [26]", "2nd hci 10.22 (15) [7]", "2 hci (15) [15]"]:
        assert not m.is_other_hci({"content": c, "labels": ["hci"]})
    assert not m.is_other_hci({"content": "deferred: nails", "labels": ["hci", "posthoc"]})
    assert not m.is_other_hci({"content": "gym", "labels": ["hcb"]})


def test_bare_future_month_means_last_year():
    jan = date(2027, 1, 26)
    assert m.parse_month("12", jan) == (2026, 12)
    assert m.parse_month("dec", jan) == (2026, 12)
    assert m.parse_month("1", jan) == (2027, 1)
