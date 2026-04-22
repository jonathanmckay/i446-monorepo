"""Regression tests for -1g-cron.py."""
import importlib.util
from pathlib import Path

MOD_PATH = Path(__file__).parent / "-1g-cron.py"
spec = importlib.util.spec_from_file_location("_1g_cron", MOD_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_time_blocks_are_chinese_earthly_branches():
    """daily-reset must write Chinese 地支, never Arabic.

    Regression: before 2026-04-21 fix, TIME_BLOCKS was Arabic (فجر, شروق, …)
    which clobbered the user's Chinese build-order format every morning at 04:00.
    """
    expected = ["卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
    assert mod.TIME_BLOCKS == expected, (
        f"TIME_BLOCKS must be Chinese 地支 {expected}, got {mod.TIME_BLOCKS}"
    )


def test_time_blocks_count():
    """Exactly 9 blocks covering 06:00–23:59 in 2-hour bands."""
    assert len(mod.TIME_BLOCKS) == 9
