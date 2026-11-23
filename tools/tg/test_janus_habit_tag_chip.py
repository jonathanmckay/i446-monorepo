"""JM 2026-11-22: "I don't see the 业写 tag on the time entries for writing in
酉". Both Toggl entries carried #业写, but janus drew chips only for value
tags, project codes and kid codes, so a habit tag (did-fast TAG_1N_HABITS,
worth 4/3 a minute) was on the entry and invisible on its row."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_habit_chip", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_habit_chip"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_habit_tag_is_a_visible_chip():
    mod = _load_tui()
    assert mod._visible_tags(["业写", "junk"]) == ["业写"]
    assert "bg:" in mod._tag_chip_style("业写")
    assert mod._tag_chip_style("junk") == ""


def test_habit_chip_set_comes_from_did_fast():
    mod = _load_tui()
    assert mod._habit_chip_tags() == frozenset(mod._df_tags_mod().TAG_1N_HABITS)


def test_habit_tag_shows_on_the_entry_row():
    mod = _load_tui()
    today = dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    mod.STATE.entries = [{"start_dt": today.replace(hour=8), "end_dt": today.replace(hour=8, minute=36),
                          "desc": "write a 5 year plan for superapp", "project_id": None,
                          "running": False, "id": 1, "tags": ["业写"]}]
    mod.STATE.entries_yday = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.entries_known = True
    mod.detail_window = lambda: (today.replace(hour=10), today.replace(hour=14))
    with patch.object(mod, "_COMPLETED_TODAY", Path("/nonexistent/completed-today.json")):
        text = "".join(t for _, t, *_ in mod.render_morning())
    assert "业写" in text


def test_did_fast_failure_hides_habit_chips_without_raising():
    mod = _load_tui()
    mod._HABIT_CHIP_TAGS = None
    with patch.object(mod, "_df_tags_mod", side_effect=RuntimeError("boom")):
        assert mod._visible_tags(["业写", "-1"]) == ["-1"]
