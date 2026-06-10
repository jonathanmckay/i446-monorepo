"""Unit tests for shorten.split_estimates — the pure parsing that keeps (N)/[N]
estimates out of the prose so they can be re-appended after Haiku shortening."""
import importlib

shorten = importlib.import_module("shorten")


def test_trailing_time_and_points():
    prose, est = shorten.split_estimates("LP scorecard: define 5 maintenance metrics (15) [30]")
    assert prose == "LP scorecard: define 5 maintenance metrics"
    assert est == "(15) [30]"


def test_no_estimates():
    prose, est = shorten.split_estimates("Reconnect QBO OAuth — unblocks m5x2 debt schedules")
    assert est == ""
    assert prose.startswith("Reconnect QBO OAuth")


def test_nested_and_brace():
    prose, est = shorten.split_estimates("150 pts ((10)) {15}")
    assert prose == "150 pts"
    assert est == "((10)) {15}"


def test_points_then_time_order_preserved():
    prose, est = shorten.split_estimates("Fill JM m5x2 roles & expectations doc [20] (20)")
    assert prose == "Fill JM m5x2 roles & expectations doc"
    assert est == "[20] (20)"


def test_g_bonus_token():
    prose, est = shorten.split_estimates("Ship the thing [0G]")
    assert prose == "Ship the thing"
    assert est == "[0G]"


def test_path_paren_not_treated_as_estimate():
    # Parenthetical paths contain non-digits and must NOT be stripped as estimates.
    prose, est = shorten.split_estimates("Hand Eldon the VP roadshow list (h335/i9/xbox)")
    assert est == ""
    assert "h335/i9/xbox" in prose


def test_short_task_below_cap_returns_none_short():
    # shorten_tasks should skip tasks whose prose is already within the cap.
    out = shorten.shorten_tasks([{"id": "x1", "content": "0t (5) [10]"}], max_new=0)
    assert "x1" not in out


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS: {fn.__name__}")
    print(f"{len(fns)} passed")
