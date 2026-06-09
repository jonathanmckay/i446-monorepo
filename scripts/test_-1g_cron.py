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


def test_archive_0g_goals_writes_durable_log(tmp_path, monkeypatch):
    """Regression: 0₲ goals must be preserved to a durable log before the daily
    reset wipes the live section (so the day's goals can be looked back on)."""
    import re as _re
    bo = tmp_path / "Build Order.md"
    bo.write_text("x")
    monkeypatch.setattr(mod, "MD_FILE", bo)

    mod._archive_0g_goals(["  - [x] RoB {20}", "  - [ ] New Org {40}"])

    log = tmp_path / "0g-log.md"
    assert log.exists(), "0g-log.md should be created"
    txt = log.read_text()
    assert "- [x] RoB {20}" in txt  # done state preserved
    assert "- [ ] New Org {40}" in txt
    assert "# 0₲ Daily Goals Log" in txt
    assert _re.search(r"## \d{4}\.\d{2}\.\d{2}", txt), "must have a dated heading"


def test_archive_0g_goals_idempotent_same_day(tmp_path, monkeypatch):
    bo = tmp_path / "Build Order.md"
    bo.write_text("x")
    monkeypatch.setattr(mod, "MD_FILE", bo)
    mod._archive_0g_goals(["  - [x] day-goal {10}"])
    mod._archive_0g_goals(["  - [x] day-goal {10}"])  # second run same day
    log = tmp_path / "0g-log.md"
    assert log.read_text().count("- [x] day-goal {10}") == 1, "must not duplicate the day"


def test_archive_0g_goals_prepends_newest_first(tmp_path, monkeypatch):
    bo = tmp_path / "Build Order.md"
    bo.write_text("x")
    monkeypatch.setattr(mod, "MD_FILE", bo)
    log = tmp_path / "0g-log.md"
    log.write_text(
        "---\ntitle: \"0₲ Goals Log\"\n---\n\n# 0₲ Daily Goals Log\n\n"
        "## 2020.01.01\n\n- [x] ancient {1}\n"
    )
    mod._archive_0g_goals(["  - [x] fresh {5}"])
    txt = log.read_text()
    assert txt.index("fresh {5}") < txt.index("ancient {1}"), "newest entry must come first"
