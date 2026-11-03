"""lib/neon/long_bonus.py — o314 → 长o314 day-total bonus."""
from __future__ import annotations

from neon import long_bonus as lb


def test_below_threshold_earns_nothing():
    assert lb.bonus_delta("o314", "", "29") is None


def test_threshold_counts_every_minute():
    assert lb.bonus_delta("o314", "", "30") == (30, 15)
    assert lb.bonus_delta("o314", "", "69.0") == (69, 34.5)


def test_crossing_threshold_credits_whole_day():
    # 20m logged first (nothing), then the day total reaches 35m
    assert lb.bonus_delta("o314", "", "20") is None
    assert lb.bonus_delta("o314", "20", "35") == (35, 17.5)
    # later 50m total: only the new 15m
    assert lb.bonus_delta("o314", "35", "50") == (15, 7.5)


def test_resend_same_total_is_noop():
    assert lb.bonus_delta("o314", "69", "69") is None


def test_correction_downward_is_negative():
    assert lb.bonus_delta("o314", "40", "25") == (-40, -20)
    assert lb.bonus_delta("o314", "45", "35") == (-10, -5)


def test_other_habits_have_no_bonus():
    assert lb.bonus_delta("冥想", "", "60") is None
    assert lb.weekly_header("o314") == "长o314"
