"""User request 2026-07-27: (1) habits block-delayed in dtd (ctrl-v →
dtd-block-snooze.json) must not show in janus's pending habit row until their
chosen block starts — "tasks that are delayed for a different block don't
show up in the 1st few rows" (that day: 1st hci, xk20, xk22, hiit);
(2) ص (prayer count) gets its own always-visible labeled counter chip
("ص 3") instead of drowning as a bare number / pending name."""
import datetime as dtm
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_snooze", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_snooze"] = mod
    spec.loader.exec_module(mod)
    return mod


def _setup(mod, tmp_path):
    mod.STATE.day_offset = 0
    mod.STATE.habits_today = []
    mod.STATE.habits_ytd = {}
    mod.STATE.prayer_count = None
    mod.STATE.events = []
    mod.BLOCK_SNOOZE = tmp_path / "dtd-block-snooze.json"
    mod._snooze_cache["mtime"] = None
    mod._snooze_cache["map"] = {}
    mod.HABIT_CARDS.clear()
    mod.HABIT_DUES.clear()


def _write_snooze(mod, snoozes, date=None):
    mod.BLOCK_SNOOZE.write_text(json.dumps(
        {"date": date or dtm.date.today().isoformat(), "snoozes": snoozes}))
    mod._snooze_cache["mtime"] = None  # bust cache (same-second mtime)


def _strip_text(mod):
    return "".join(t for _, t in mod.render_habits_today())


def test_snoozed_habit_hidden_until_its_block(tmp_path):
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today().isoformat()
    mod.HABIT_CARDS["hiit"] = [("id-hiit", today)]
    mod.STATE.habits_today = [("hiit", None), ("0l", None)]
    future_h = min(23, dtm.datetime.now(TZ).hour + 2)
    _write_snooze(mod, {"id-hiit": future_h})
    text = _strip_text(mod)
    assert "hiit" not in text, "block-snoozed habit must leave the pending row"
    assert "0l" in text, "un-snoozed habits stay"


def test_snoozed_habit_reappears_once_block_starts(tmp_path):
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today().isoformat()
    mod.HABIT_CARDS["hiit"] = [("id-hiit", today)]
    mod.STATE.habits_today = [("hiit", None)]
    _write_snooze(mod, {"id-hiit": dtm.datetime.now(TZ).hour})  # block has started
    assert "hiit" in _strip_text(mod)


def test_stale_snooze_file_ignored(tmp_path):
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today().isoformat()
    mod.HABIT_CARDS["hiit"] = [("id-hiit", today)]
    mod.STATE.habits_today = [("hiit", None)]
    _write_snooze(mod, {"id-hiit": 23}, date="2020-01-01")
    assert "hiit" in _strip_text(mod), "yesterday's snoozes must not hide today"


def test_done_habit_unaffected_by_snooze(tmp_path):
    """A habit with a value renders in the done row regardless of snooze."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    today = dtm.date.today().isoformat()
    mod.HABIT_CARDS["hiit"] = [("id-hiit", today)]
    mod.STATE.habits_today = [("hiit", 10.0)]
    _write_snooze(mod, {"id-hiit": 23})
    assert "10" in _strip_text(mod)


def test_prayer_counter_chip_labeled_after_ytd_chips(tmp_path):
    """Placement follow-up (2026-07-27): ص closes the SECOND line, after the
    其他人 YTD chip — not leading the done row."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    mod.STATE.prayer_count = 3.0
    mod.STATE.habits_today = [("0l", 2.0), ("hiit", None)]
    mod.STATE.habits_ytd = {"其他人": -2.0}
    text = _strip_text(mod)
    assert "ص 3" in text
    lines = text.split("\n")
    assert "ص 3" in lines[1], "counter chip lives on the pending/YTD line"
    assert lines[1].index("ص 3") > lines[1].index("其他人")


def test_prayer_zero_still_shown(tmp_path):
    """0 prayers is information ("none yet"), not an empty state."""
    mod = _load_tui()
    _setup(mod, tmp_path)
    mod.STATE.prayer_count = 0.0
    mod.STATE.habits_today = [("0l", None)]
    assert "ص 0" in _strip_text(mod)


def test_fetch_loop_diverts_prayer_from_habit_rows():
    """Source-level: fetch_habits_today must capture ص for the counter and
    never emit it as an ordinary done/pending habit."""
    src = (HERE / "janus.py").read_text()
    i = src.index("def fetch_habits_today")
    body = src[i:src.index("\ndef ", i + 10)]
    assert 'name == "ص"' in body
    assert "prayer_count" in body


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
